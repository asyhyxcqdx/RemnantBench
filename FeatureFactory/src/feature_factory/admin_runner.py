from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from threading import Condition, Lock

from sqlalchemy.orm import Session, sessionmaker

from feature_factory.config import Settings
from feature_factory.models import CrawlJob
from feature_factory.stage1.github_client import GitHubSearchClient
from feature_factory.stage1.schemas import CrawlFilters
from feature_factory.stage1.service import CrawlService
from feature_factory.stage1.token_scheduler import GitHubTokenScheduler


class Stage1JobRunner:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        max_workers: int = 4,
        max_limit: int = 24,
        token_scheduler: GitHubTokenScheduler | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.token_scheduler = token_scheduler or GitHubTokenScheduler(settings)
        if max_workers > settings.stage1_max_concurrent_jobs_cap:
            raise ValueError(
                f"max_workers cannot exceed configured stage1 job cap {settings.stage1_max_concurrent_jobs_cap}"
            )
        resolved_max_limit = min(max_limit, settings.stage1_max_concurrent_jobs_cap)
        self.max_limit = max(resolved_max_limit, max_workers)
        self.partition_concurrency_default = settings.stage1_default_max_concurrent_partitions_per_job
        self.partition_concurrency_cap = settings.stage1_max_concurrent_partitions_per_job_cap
        self.executor = ThreadPoolExecutor(max_workers=self.max_limit, thread_name_prefix="ff-stage1")
        self._lock = Lock()
        self._futures: dict[str, Future[None]] = {}
        self._claimed_jobs: set[str] = set()
        self._running_jobs: set[str] = set()
        self._pause_requests: set[str] = set()
        self._cancel_requests: set[str] = set()
        self._capacity = Condition(Lock())
        self._max_workers = max_workers
        self._active_jobs = 0

    def schedule_job(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> bool:
        with self._lock:
            active = self._futures.get(job_id)
            if active is not None and not active.done():
                return False
            self._pause_requests.discard(job_id)
            self._cancel_requests.discard(job_id)
            future = self.executor.submit(
                self._run_job_future,
                job_id,
                max_concurrent_partitions=max_concurrent_partitions,
            )
            self._futures[job_id] = future
            future.add_done_callback(
                lambda completed_future, scheduled_job_id=job_id: self._cleanup_future(
                    scheduled_job_id,
                    completed_future,
                )
            )
            return True

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._running_jobs

    def is_scheduled(self, job_id: str) -> bool:
        with self._lock:
            future = self._futures.get(job_id)
            return future is not None and not future.done()

    def request_pause(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._running_jobs:
                return False
            self._pause_requests.add(job_id)
            return True

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            future = self._futures.get(job_id)
            if (
                future is None
                or future.done()
                or job_id in self._claimed_jobs
                or job_id in self._running_jobs
            ):
                return False
            self._cancel_requests.add(job_id)
        with self._capacity:
            self._capacity.notify_all()
        return True

    def is_pause_requested(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._pause_requests

    def get_max_workers(self) -> int:
        with self._capacity:
            return self._max_workers

    def set_max_workers(self, max_workers: int) -> None:
        if max_workers < 1:
            raise ValueError("max_concurrent_jobs must be at least 1")
        if max_workers > self.max_limit:
            raise ValueError(f"max_concurrent_jobs cannot exceed {self.max_limit}")
        with self._capacity:
            self._max_workers = max_workers
            self._capacity.notify_all()

    def get_default_max_concurrent_partitions(self) -> int:
        return self.partition_concurrency_default

    def validate_max_concurrent_partitions(self, requested: int | None) -> int:
        return self._resolve_max_concurrent_partitions(requested)

    def _run_job_future(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> None:
        acquired_slot = self._acquire_slot(job_id)
        if not acquired_slot:
            with self._lock:
                self._claimed_jobs.discard(job_id)
                self._pause_requests.discard(job_id)
                self._cancel_requests.discard(job_id)
            return
        try:
            with self._lock:
                self._claimed_jobs.discard(job_id)
                self._running_jobs.add(job_id)
            self._execute_job(
                job_id,
                max_concurrent_partitions=max_concurrent_partitions,
            )
        finally:
            with self._lock:
                self._claimed_jobs.discard(job_id)
                self._running_jobs.discard(job_id)
                self._pause_requests.discard(job_id)
                self._cancel_requests.discard(job_id)
            self._release_slot()

    def run_job(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> None:
        self._run_job_future(
            job_id,
            max_concurrent_partitions=max_concurrent_partitions,
        )

    def _execute_job(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> None:
        partition_executor: ThreadPoolExecutor | None = None
        try:
            if self._run_target_repository_job_if_needed(job_id):
                return
            worker_count = self._resolve_max_concurrent_partitions(max_concurrent_partitions)
            if self._repository_limit_for_job(job_id) is not None:
                worker_count = 1
            partition_ids = self._prepare_job_execution(job_id)
            if not partition_ids:
                return

            if worker_count <= 1 or len(partition_ids) <= 1:
                for partition_id in partition_ids:
                    if self.is_pause_requested(job_id):
                        self._pause_or_finalize_job(job_id, pause_requested=True)
                        return
                    paused_during_partition = self._run_partition_task(
                        job_id,
                        partition_id,
                    )
                    self._sync_job_progress(job_id, is_running=True)
                    if paused_during_partition:
                        self._pause_or_finalize_job(job_id, pause_requested=True)
                        return
                self._pause_or_finalize_job(job_id, pause_requested=False)
                return

            partition_pool_size = min(worker_count, len(partition_ids))
            partition_executor = ThreadPoolExecutor(
                max_workers=partition_pool_size,
                thread_name_prefix=f"ff-stage1-partition-{job_id[:8]}",
            )
            partition_iter = iter(partition_ids)
            running: dict[Future[bool], str] = {}
            first_error: Exception | None = None
            pause_requested = False

            def schedule_more() -> None:
                while (
                    first_error is None
                    and not pause_requested
                    and len(running) < partition_pool_size
                ):
                    try:
                        partition_id = next(partition_iter)
                    except StopIteration:
                        return
                    future = partition_executor.submit(
                        self._run_partition_task,
                        job_id,
                        partition_id,
                    )
                    running[future] = partition_id

            schedule_more()
            while running:
                completed, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
                for future in completed:
                    running.pop(future, None)
                    try:
                        paused_during_partition = future.result()
                    except Exception as exc:
                        if first_error is None:
                            first_error = exc
                    else:
                        pause_requested = (
                            pause_requested
                            or paused_during_partition
                            or self.is_pause_requested(job_id)
                        )
                    finally:
                        self._sync_job_progress(job_id, is_running=True)
                if first_error is None and not pause_requested:
                    schedule_more()

            if first_error is not None:
                raise first_error
            self._pause_or_finalize_job(job_id, pause_requested=pause_requested or self.is_pause_requested(job_id))
        except Exception as exc:
            session = self.session_factory()
            client = GitHubSearchClient(self.settings, token_scheduler=self.token_scheduler)
            try:
                service = CrawlService(session, self.settings, client)
                service.mark_job_failed(job_id, str(exc))
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                client.close()
                session.close()
            raise
        finally:
            if partition_executor is not None:
                partition_executor.shutdown(wait=True, cancel_futures=False)

    def _run_target_repository_job_if_needed(self, job_id: str) -> bool:
        filters = self._filters_for_job(job_id)
        if filters is None or not filters.target_repositories:
            return False
        session = self.session_factory()
        client = GitHubSearchClient(self.settings, token_scheduler=self.token_scheduler)
        try:
            service = CrawlService(session, self.settings, client)
            handled = service.run_target_repository_job(
                job_id,
                should_pause=lambda: self.is_pause_requested(job_id),
                persist_progress=session.commit,
            )
            if handled:
                session.commit()
            return handled
        except Exception:
            try:
                session.commit()
            except Exception:
                session.rollback()
            raise
        finally:
            client.close()
            session.close()

    def shutdown(self, *, timeout_seconds: float | None = None) -> list[str]:
        with self._lock:
            running_job_ids = set(self._running_jobs)
            active_futures = {
                job_id: future
                for job_id, future in self._futures.items()
                if not future.done()
            }
            for job_id in active_futures:
                if job_id in running_job_ids:
                    self._pause_requests.add(job_id)
                else:
                    self._cancel_requests.add(job_id)
        with self._capacity:
            self._capacity.notify_all()
        if timeout_seconds is None:
            self.executor.shutdown(wait=True, cancel_futures=True)
            return []
        self.executor.shutdown(wait=False, cancel_futures=True)
        if not active_futures:
            return []
        _, not_done = wait(tuple(active_futures.values()), timeout=max(timeout_seconds, 0.0))
        return [
            job_id
            for job_id, future in active_futures.items()
            if future in not_done and not future.done()
        ]

    def _resolve_max_concurrent_partitions(self, requested: int | None) -> int:
        resolved = requested or self.partition_concurrency_default
        if resolved < 1:
            raise ValueError("max_concurrent_partitions must be at least 1")
        if resolved > self.partition_concurrency_cap:
            raise ValueError(
                f"max_concurrent_partitions cannot exceed {self.partition_concurrency_cap}"
            )
        return resolved

    def _cleanup_future(self, job_id: str, future: Future[None]) -> None:
        with self._lock:
            active = self._futures.get(job_id)
            if active is future and future.done():
                self._futures.pop(job_id, None)

    def _prepare_job_execution(
        self,
        job_id: str,
    ) -> list[str]:
        session = self.session_factory()
        client = GitHubSearchClient(self.settings, token_scheduler=self.token_scheduler)
        try:
            service = CrawlService(session, self.settings, client)
            partition_ids = service.prepare_job_run(
                job_id,
                should_pause=lambda: self.is_pause_requested(job_id),
                persist_progress=session.commit,
            )
            return partition_ids
        finally:
            client.close()
            session.close()

    def _repository_limit_for_job(self, job_id: str) -> int | None:
        filters = self._filters_for_job(job_id)
        return filters.repository_limit if filters is not None else None

    def _filters_for_job(self, job_id: str) -> CrawlFilters | None:
        session = self.session_factory()
        try:
            job = session.get(CrawlJob, job_id)
            if job is None:
                return None
            try:
                return CrawlFilters.model_validate(job.filters_json)
            except Exception:  # noqa: BLE001
                return None
        finally:
            session.close()

    def _run_partition_task(
        self,
        job_id: str,
        partition_id: str,
    ) -> bool:
        session = self.session_factory()
        client = GitHubSearchClient(self.settings, token_scheduler=self.token_scheduler)
        try:
            service = CrawlService(session, self.settings, client)
            paused_during_partition = service.execute_partition(
                job_id,
                partition_id,
                should_pause=lambda: self.is_pause_requested(job_id),
                persist_progress=session.commit,
            )
            session.commit()
            return paused_during_partition
        except Exception:
            try:
                session.commit()
            except Exception:
                session.rollback()
            raise
        finally:
            client.close()
            session.close()

    def _sync_job_progress(self, job_id: str, *, is_running: bool) -> None:
        session = self.session_factory()
        client = GitHubSearchClient(self.settings, token_scheduler=self.token_scheduler)
        try:
            service = CrawlService(session, self.settings, client)
            service.finalize_job(job_id, persist_progress=session.commit, is_running=is_running)
        finally:
            client.close()
            session.close()

    def _pause_or_finalize_job(self, job_id: str, *, pause_requested: bool) -> None:
        session = self.session_factory()
        client = GitHubSearchClient(self.settings, token_scheduler=self.token_scheduler)
        try:
            service = CrawlService(session, self.settings, client)
            if pause_requested and service.has_pending_partitions(job_id):
                service.mark_job_paused(job_id, persist_progress=session.commit)
            else:
                service.finalize_job(job_id, persist_progress=session.commit, is_running=False)
        finally:
            client.close()
            session.close()

    def _acquire_slot(self, job_id: str) -> bool:
        with self._capacity:
            while self._active_jobs >= self._max_workers:
                with self._lock:
                    if job_id in self._cancel_requests:
                        return False
                self._capacity.wait(timeout=0.2)
            with self._lock:
                if job_id in self._cancel_requests:
                    return False
                self._claimed_jobs.add(job_id)
            self._active_jobs += 1
            return True

    def _release_slot(self) -> None:
        with self._capacity:
            self._active_jobs = max(self._active_jobs - 1, 0)
            self._capacity.notify_all()