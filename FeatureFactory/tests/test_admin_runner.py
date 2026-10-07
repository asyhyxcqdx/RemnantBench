import threading
from datetime import UTC, datetime
import time
from threading import Event, Lock

from feature_factory.models import CrawlJob, CrawlPartition, GitHubRepository, RepoDiscovery
from feature_factory.admin_runner import Stage1JobRunner
from feature_factory.config import Settings
from feature_factory.db import build_engine, build_session_factory, init_db


class BlockingRunner(Stage1JobRunner):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.started: dict[str, Event] = {}
        self.releases: dict[str, Event] = {}

    def _execute_job(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> None:
        started = self.started.setdefault(job_id, Event())
        release = self.releases.setdefault(job_id, Event())
        started.set()
        release.wait(timeout=2.0)


class ClaimWindowRunner(Stage1JobRunner):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.after_acquire = Event()
        self.resume = Event()
        self.executed = Event()

    def _run_job_future(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> None:
        acquired_slot = self._acquire_slot(job_id)
        if not acquired_slot:
            return
        self.after_acquire.set()
        self.resume.wait(timeout=2.0)
        try:
            with self._lock:
                self._claimed_jobs.discard(job_id)
                self._running_jobs.add(job_id)
            self.executed.set()
            time.sleep(0.02)
        finally:
            with self._lock:
                self._claimed_jobs.discard(job_id)
                self._running_jobs.discard(job_id)
                self._pause_requests.discard(job_id)
                self._cancel_requests.discard(job_id)
            self._release_slot()


class InstantRunner(Stage1JobRunner):
    def _execute_job(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> None:
        return None


class PartitionTrackingRunner(Stage1JobRunner):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.partition_started: list[str] = []
        self.max_active_partitions = 0
        self._active_partitions = 0
        self._partition_lock = Lock()
        self._release = Event()
        self.pause_requested: bool | None = None

    def _prepare_job_execution(
        self,
        job_id: str,
    ) -> list[str]:
        return ["partition-1", "partition-2", "partition-3"]

    def _run_partition_task(
        self,
        job_id: str,
        partition_id: str,
    ) -> bool:
        with self._partition_lock:
            self._active_partitions += 1
            self.max_active_partitions = max(self.max_active_partitions, self._active_partitions)
            self.partition_started.append(partition_id)
            if len(self.partition_started) >= 2:
                self._release.set()
        self._release.wait(timeout=2.0)
        time.sleep(0.02)
        with self._partition_lock:
            self._active_partitions -= 1
        return False

    def _sync_job_progress(self, job_id: str, *, is_running: bool) -> None:
        return None

    def _pause_or_finalize_job(self, job_id: str, *, pause_requested: bool) -> None:
        self.pause_requested = pause_requested


class PartialFailureRunner(Stage1JobRunner):
    def _prepare_job_execution(
        self,
        job_id: str,
    ) -> list[str]:
        return ["partition-1", "partition-2"]

    def _run_partition_task(
        self,
        job_id: str,
        partition_id: str,
    ) -> bool:
        session = self.session_factory()
        try:
            partition = session.get(CrawlPartition, partition_id)
            assert partition is not None
            if partition_id == "partition-1":
                repository = GitHubRepository(
                    github_repo_id=4001,
                    full_name="owner/partial-repo",
                    owner_login="owner",
                    name="partial-repo",
                    html_url="https://github.com/owner/partial-repo",
                    api_url="https://api.github.com/repos/owner/partial-repo",
                    description="partial repo",
                    primary_language="Python",
                    license_key="mit",
                    stargazers_count=1,
                    created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
                    pushed_at_github=datetime(2024, 1, 2, tzinfo=UTC),
                    discovered_at=datetime(2024, 1, 3, tzinfo=UTC),
                )
                session.add(repository)
                session.flush()
                session.add(
                    RepoDiscovery(
                        job_id=job_id,
                        partition_id=partition_id,
                        repository_id=repository.id,
                        query_string=partition.query_string,
                        discovered_at=datetime(2024, 1, 3, tzinfo=UTC),
                    )
                )
                partition.status = "completed"
                partition.fetched_count = 1
                partition.unique_count = 1
                partition.started_at = datetime.now(UTC)
                partition.finished_at = datetime.now(UTC)
                session.commit()
                return False

            partition.status = "failed"
            partition.error_message = "partition boom"
            partition.started_at = datetime.now(UTC)
            partition.finished_at = datetime.now(UTC)
            session.commit()
            raise RuntimeError("partition boom")
        finally:
            session.close()

    def _sync_job_progress(self, job_id: str, *, is_running: bool) -> None:
        return None


class GracefulShutdownRunner(Stage1JobRunner):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.started: dict[str, Event] = {}
        self.executed: list[str] = []
        self.pause_seen: set[str] = set()

    def _execute_job(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> None:
        self.executed.append(job_id)
        started = self.started.setdefault(job_id, Event())
        started.set()
        if job_id != "job-1":
            return
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if self.is_pause_requested(job_id):
                self.pause_seen.add(job_id)
                return
            time.sleep(0.01)

    def _sync_job_progress(self, job_id: str, *, is_running: bool) -> None:
        return None

    def _pause_or_finalize_job(self, job_id: str, *, pause_requested: bool) -> None:
        return None


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not satisfied before timeout")


def test_stage1_job_runner_treats_queued_jobs_as_scheduled_but_not_running(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'runner.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = BlockingRunner(session_factory, settings, max_workers=1, max_limit=2)

    try:
        assert runner.schedule_job("job-1") is True
        _wait_until(lambda: runner.started.get("job-1", Event()).is_set())
        assert runner.is_scheduled("job-1") is True
        assert runner.is_running("job-1") is True

        assert runner.schedule_job("job-2") is True
        time.sleep(0.05)
        assert runner.is_scheduled("job-2") is True
        assert runner.is_running("job-2") is False

        runner.releases["job-1"].set()
        _wait_until(lambda: runner.started.get("job-2", Event()).is_set())
        assert runner.is_running("job-2") is True

        runner.releases["job-2"].set()
        _wait_until(lambda: not runner.is_scheduled("job-2"))
    finally:
        runner.shutdown()


def test_stage1_job_runner_shutdown_respects_timeout(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'runner-shutdown-timeout.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = BlockingRunner(session_factory, settings, max_workers=1, max_limit=1)

    try:
        assert runner.schedule_job("job-1") is True
        _wait_until(lambda: runner.started.get("job-1", Event()).is_set())
        started_at = time.monotonic()
        unfinished = runner.shutdown(timeout_seconds=0.05)
        elapsed = time.monotonic() - started_at
        assert elapsed < 0.5
        assert unfinished == ["job-1"]
    finally:
        runner.releases.setdefault("job-1", Event()).set()
        _wait_until(lambda: not runner.is_scheduled("job-1"))


def test_stage1_job_runner_can_cancel_queued_job_before_it_starts(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'runner-cancel.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = BlockingRunner(session_factory, settings, max_workers=1, max_limit=2)

    try:
        assert runner.schedule_job("job-1") is True
        _wait_until(lambda: runner.started.get("job-1", Event()).is_set())
        assert runner.schedule_job("job-2") is True
        time.sleep(0.05)
        assert runner.is_scheduled("job-2") is True
        assert runner.cancel_job("job-2") is True

        runner.releases["job-1"].set()
        _wait_until(lambda: not runner.is_scheduled("job-2"))
        assert "job-2" not in runner.started
        assert runner.is_running("job-2") is False
    finally:
        runner.shutdown()


def test_stage1_job_runner_only_allows_pause_for_running_jobs(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'runner-pause-running-only.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = BlockingRunner(session_factory, settings, max_workers=1, max_limit=2)

    try:
        assert runner.schedule_job("job-1") is True
        _wait_until(lambda: runner.started.get("job-1", Event()).is_set())
        assert runner.request_pause("job-1") is True
        assert runner.schedule_job("job-2") is True
        time.sleep(0.05)
        assert runner.is_scheduled("job-2") is True
        assert runner.is_running("job-2") is False
        assert runner.request_pause("job-2") is False

        runner.releases["job-1"].set()
        _wait_until(lambda: runner.started.get("job-2", Event()).is_set())
        runner.releases["job-2"].set()
        _wait_until(lambda: not runner.is_scheduled("job-2"))
    finally:
        runner.shutdown()


def test_stage1_job_runner_cleans_up_completed_futures(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'runner-future-cleanup.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = InstantRunner(session_factory, settings, max_workers=1, max_limit=1)

    try:
        for index in range(10):
            assert runner.schedule_job(f"job-{index}") is True
        _wait_until(lambda: all(not runner.is_scheduled(f"job-{index}") for index in range(10)))
        _wait_until(lambda: len(runner._futures) == 0)
    finally:
        runner.shutdown()


def test_stage1_job_runner_cannot_cancel_job_after_slot_is_claimed(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'runner-claim-window.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = ClaimWindowRunner(session_factory, settings, max_workers=1, max_limit=1)

    try:
        assert runner.schedule_job("job-1") is True
        _wait_until(lambda: runner.after_acquire.is_set())
        assert runner.is_scheduled("job-1") is True
        assert runner.is_running("job-1") is False
        assert runner.cancel_job("job-1") is False

        runner.resume.set()
        _wait_until(lambda: runner.executed.is_set())
        _wait_until(lambda: not runner.is_scheduled("job-1"))
    finally:
        runner.shutdown()


def test_stage1_job_runner_executes_partitions_up_to_requested_concurrency(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'runner-partitions.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = PartitionTrackingRunner(session_factory, settings, max_workers=1, max_limit=1)

    try:
        runner.run_job("job-1", max_concurrent_partitions=2)
        assert runner.max_active_partitions == 2
        assert runner.partition_started == ["partition-1", "partition-2", "partition-3"]
        assert runner.pause_requested is False
    finally:
        runner.shutdown()


def test_stage1_job_runner_marks_partially_completed_job_as_partial_on_failure(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'runner-partial-fail.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    session = session_factory()
    try:
        job = CrawlJob(
            id="job-partial",
            name="partial-failure-job",
            status="running",
            filters_json={},
            stats_json={},
            started_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        session.add(job)
        session.add(
            CrawlPartition(
                id="partition-1",
                job_id=job.id,
                status="pending",
                depth=0,
                range_start=datetime(2024, 1, 1, tzinfo=UTC),
                range_end=datetime(2024, 1, 1, tzinfo=UTC),
                query_string="language:Python",
                expected_count=1,
            )
        )
        session.add(
            CrawlPartition(
                id="partition-2",
                job_id=job.id,
                status="pending",
                depth=0,
                range_start=datetime(2024, 1, 2, tzinfo=UTC),
                range_end=datetime(2024, 1, 2, tzinfo=UTC),
                query_string="language:Go",
                expected_count=1,
            )
        )
        session.commit()
    finally:
        session.close()

    runner = PartialFailureRunner(session_factory, settings, max_workers=1, max_limit=1)
    try:
        try:
            runner._execute_job("job-partial")
        except RuntimeError as exc:
            assert str(exc) == "partition boom"
        else:
            raise AssertionError("expected runner to re-raise partition failure")

        verify = session_factory()
        try:
            job = verify.get(CrawlJob, "job-partial")
            assert job is not None
            assert job.status == "partial"
            assert job.error_message == "partition boom"
            assert job.stats_json["partition_status_counts"] == {"completed": 1, "failed": 1}
            assert job.stats_json["unique_repositories"] == 1
            assert job.stats_json["repository_hits"] == 1
        finally:
            verify.close()
    finally:
        runner.shutdown()


def test_stage1_job_runner_shutdown_pauses_running_jobs_and_cancels_waiting_jobs(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'runner-shutdown.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = GracefulShutdownRunner(session_factory, settings, max_workers=1, max_limit=2)

    try:
        assert runner.schedule_job("job-1") is True
        _wait_until(lambda: runner.started.get("job-1", Event()).is_set())

        assert runner.schedule_job("job-2") is True
        time.sleep(0.05)

        shutdown_thread = threading.Thread(target=runner.shutdown)
        shutdown_thread.start()
        shutdown_thread.join(timeout=1.0)

        assert shutdown_thread.is_alive() is False
        assert runner.pause_seen == {"job-1"}
        assert runner.executed == ["job-1"]
        assert runner.is_scheduled("job-2") is False
    finally:
        runner.shutdown()
