from __future__ import annotations

import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from feature_factory.config import Settings
from feature_factory.stage2.validator import (
    CommandResult,
    Stage2Validator,
    validate_artifact_schema,
)
from feature_factory.stage3.runtime_images import (
    detect_stage3_platform,
    ensure_stage3_breaker_runtime_image_built,
)

Stage3EvaluationEvent = Callable[[str, str, dict[str, Any]], None]
FEATURE_FACTORY_DOCKER_LABEL_MANAGED = "feature_factory.managed"
FEATURE_FACTORY_DOCKER_LABEL_STAGE = "feature_factory.stage"
FEATURE_FACTORY_DOCKER_LABEL_COMPONENT = "feature_factory.component"
FEATURE_FACTORY_DOCKER_LABEL_RUN_ID = "feature_factory.run_id"
FEATURE_FACTORY_DOCKER_LABEL_TEST_FILE = "feature_factory.test_file"
FEATURE_FACTORY_DOCKER_LABEL_CREATED_AT = "feature_factory.created_at"
FEATURE_FACTORY_STAGE3_EVAL_COMPONENT = "stage3-eval"


class Stage3EvaluationError(RuntimeError):
    def __init__(self, message: str, *, code: str, evidence: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.evidence = dict(evidence or {})


@dataclass(frozen=True, slots=True)
class Stage3FileEvaluation:
    test_file_path: str
    status: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    error_tests: int
    skipped_tests: int
    pass_rate: float
    raw_result_json: dict[str, Any]
    target_selector: str | None = None


@dataclass(frozen=True, slots=True)
class Stage3EvaluationResult:
    image_tag: str
    file_results: list[Stage3FileEvaluation]


def _normalize_original_p2p_file(item: Any) -> dict[str, str]:
    if isinstance(item, dict):
        path = str(item.get("path") or item.get("test_file_path") or "").strip()
        target_selector = str(item.get("target_selector") or path).strip()
    else:
        path = str(item or "").strip()
        target_selector = path
    return {
        "path": path,
        "target_selector": target_selector or path,
    }


class Stage3Evaluator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._validator = Stage2Validator(settings)

    def evaluate_original_p2p(
        self,
        *,
        run_id: str,
        workspace_dir: Path,
        repo_dir: Path,
        snapshot_id: str,
        source_stage2_run_id: str,
        source_commit_sha: str,
        base_image_id: str,
        dockerfile_text: str,
        run_script_text: str,
        original_p2p_files: list[Any],
        emit_event: Stage3EvaluationEvent | None = None,
    ) -> Stage3EvaluationResult:
        if shutil.which("docker") is None:
            raise Stage3EvaluationError("docker is required for stage3 savepoint evaluation", code="DOCKER_MISSING")

        validate_artifact_schema(dockerfile_text=dockerfile_text, run_script_text=run_script_text)
        try:
            image_tag = ensure_stage3_breaker_runtime_image_built(
                workspace_dir=workspace_dir,
                snapshot_id=snapshot_id,
                source_stage2_run_id=source_stage2_run_id,
                source_commit_sha=source_commit_sha,
                base_image_id=base_image_id,
                platform_name=detect_stage3_platform(),
                timeout_seconds=self.settings.stage3_build_timeout_seconds,
                emit_event=emit_event,
            )
        except RuntimeError as exc:
            raise Stage3EvaluationError(
                "docker build failed during stage3 savepoint evaluation",
                code="DOCKER_BUILD_FAILED",
                evidence={"message": str(exc)},
            ) from exc
        run_script_host_path = self._validator._resolve_run_script_host_path(  # noqa: SLF001
            workspace_dir=workspace_dir,
            run_script_text=run_script_text,
        )

        file_results: list[Stage3FileEvaluation] = []
        normalized_original_p2p_files = [_normalize_original_p2p_file(item) for item in original_p2p_files]
        total_files = len(normalized_original_p2p_files)
        run_test_timeout_seconds = float(self.settings.stage3_run_test_timeout_seconds)
        full_validation_timeout_seconds = float(self.settings.stage3_full_validation_timeout_seconds)
        deadline = time.monotonic() + full_validation_timeout_seconds
        evaluation_parent = workspace_dir / ".stage3" / "evaluation"
        evaluation_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"{self._safe_path_part(run_id)}-",
            dir=evaluation_parent,
        ) as tmp_root:
            evaluation_root = Path(tmp_root)
            template_repo_dir = self._create_evaluation_template(
                repo_dir=repo_dir,
                evaluation_root=evaluation_root,
                emit_event=emit_event,
            )
            for index, test_file in enumerate(normalized_original_p2p_files, start=1):
                test_file_path = test_file["path"]
                target_selector = test_file["target_selector"]
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    evidence: dict[str, Any] = {
                        "timeout_seconds": full_validation_timeout_seconds,
                        "completed_files": len(file_results),
                        "total_files": total_files,
                        "next_test_file_path": test_file_path,
                    }
                    if target_selector != test_file_path:
                        evidence["next_target_selector"] = target_selector
                    raise Stage3EvaluationError(
                        (
                            "stage3 full validation exceeded timeout before all original P2P files completed"
                        ),
                        code="FULL_VALIDATION_TIMEOUT",
                        evidence=evidence,
                    )
                timeout_seconds = min(run_test_timeout_seconds, remaining_seconds)
                hit_full_timeout = timeout_seconds < run_test_timeout_seconds
                isolated_repo_dir = self._create_isolated_test_repo(
                    template_repo_dir=template_repo_dir,
                    evaluation_root=evaluation_root,
                    test_file_index=index,
                )
                try:
                    if emit_event is not None:
                        emit_event(
                            "Stage3 test started",
                            f"Running original P2P file {index}/{total_files}: {test_file_path}",
                            {
                                "test_file_path": test_file_path,
                                "target_selector": target_selector,
                                "test_file_index": index,
                                "test_file_total": total_files,
                                "image_tag": image_tag,
                                "isolated_repo": True,
                            },
                        )
                    command_result = self._run_test_file(
                        run_id=run_id,
                        image_tag=image_tag,
                        repo_dir=isolated_repo_dir,
                        run_script_host_path=run_script_host_path,
                        test_file_path=test_file_path,
                        target_selector=target_selector,
                        timeout_seconds=timeout_seconds,
                    )
                finally:
                    shutil.rmtree(isolated_repo_dir.parent, ignore_errors=True)
                if command_result.timed_out and hit_full_timeout:
                    evidence = {
                        "timeout_seconds": full_validation_timeout_seconds,
                        "completed_files": len(file_results),
                        "total_files": total_files,
                        "timed_out_test_file_path": test_file_path,
                    }
                    if target_selector != test_file_path:
                        evidence["timed_out_target_selector"] = target_selector
                    raise Stage3EvaluationError(
                        "stage3 full validation exceeded timeout while running an original P2P file",
                        code="FULL_VALIDATION_TIMEOUT",
                        evidence=evidence,
                    )
                payload = self._validator._maybe_load_json_from_stdout(command_result)  # noqa: SLF001
                schema_error = None if payload is None else self._validator._validate_run_payload(payload)  # noqa: SLF001
                normalized = self._validator._normalize_file_result(  # noqa: SLF001
                    {
                        "test_file_path": test_file_path,
                        "target_selector": target_selector,
                        "command": self._validator._command_evidence(command_result),  # noqa: SLF001
                        "payload": payload,
                        "schema_error": schema_error,
                    }
                )
                result_payload = dict(normalized["result"] or {})
                summary = dict(result_payload.get("summary") or {})
                total_tests = int(summary.get("collected") or 0)
                passed_tests = int(summary.get("passed") or 0)
                failed_tests = int(summary.get("failed") or 0)
                error_tests = int(summary.get("errors") or 0)
                skipped_tests = int(summary.get("skipped") or 0)
                pass_rate = float(passed_tests) / float(total_tests) if total_tests > 0 else 0.0
                file_results.append(
                    Stage3FileEvaluation(
                        test_file_path=test_file_path,
                        status=str(result_payload.get("status") or "error"),
                        total_tests=total_tests,
                        passed_tests=passed_tests,
                        failed_tests=failed_tests,
                        error_tests=error_tests,
                        skipped_tests=skipped_tests,
                        pass_rate=pass_rate,
                        raw_result_json=result_payload,
                        target_selector=target_selector,
                    )
                )
                if emit_event is not None:
                    emit_event(
                        "Stage3 test completed",
                        (
                            f"Completed original P2P file {index}/{total_files}: {test_file_path} "
                            f"({result_payload.get('status') or 'error'})"
                        ),
                        {
                            "test_file_path": test_file_path,
                            "target_selector": target_selector,
                            "test_file_index": index,
                            "test_file_total": total_files,
                            "status": result_payload.get("status") or "error",
                            "summary": summary,
                            "image_tag": image_tag,
                            "isolated_repo": True,
                        },
                    )

        return Stage3EvaluationResult(image_tag=image_tag, file_results=file_results)

    def _create_evaluation_template(
        self,
        *,
        repo_dir: Path,
        evaluation_root: Path,
        emit_event: Stage3EvaluationEvent | None,
    ) -> Path:
        template_repo_dir = evaluation_root / "template" / "repo"
        try:
            self._copy_repo_tree(repo_dir, template_repo_dir)
        except OSError as exc:
            raise Stage3EvaluationError(
                "failed to snapshot the stage3 evaluation repository",
                code="EVALUATION_REPO_SNAPSHOT_FAILED",
                evidence={
                    "repo_dir": str(repo_dir),
                    "template_repo_dir": str(template_repo_dir),
                    "message": str(exc),
                },
            ) from exc
        if emit_event is not None:
            emit_event(
                "Stage3 evaluation repo snapshotted",
                "Created an isolated template repository for original P2P evaluation",
                {
                    "repo_dir": str(repo_dir),
                    "isolated_repo": True,
                },
            )
        return template_repo_dir

    def _create_isolated_test_repo(
        self,
        *,
        template_repo_dir: Path,
        evaluation_root: Path,
        test_file_index: int,
    ) -> Path:
        test_repo_dir = evaluation_root / f"file-{test_file_index:04d}" / "repo"
        try:
            self._copy_repo_tree(template_repo_dir, test_repo_dir)
        except OSError as exc:
            raise Stage3EvaluationError(
                "failed to create an isolated repository for stage3 test execution",
                code="EVALUATION_REPO_COPY_FAILED",
                evidence={
                    "template_repo_dir": str(template_repo_dir),
                    "test_repo_dir": str(test_repo_dir),
                    "test_file_index": test_file_index,
                    "message": str(exc),
                },
            ) from exc
        return test_repo_dir

    def _copy_repo_tree(self, source: Path, destination: Path) -> None:
        source = source.expanduser().resolve()
        destination = destination.expanduser().resolve()
        if not source.exists():
            raise OSError(f"source repository does not exist: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self._copy_repo_tree_with_cow(source, destination):
            _make_tree_container_writable(destination)
            return
        shutil.copytree(source, destination, symlinks=True)
        _make_tree_container_writable(destination)

    def _copy_repo_tree_with_cow(self, source: Path, destination: Path) -> bool:
        if shutil.which("cp") is None:
            return False
        command = (
            ["cp", "-cR", str(source), str(destination)]
            if sys.platform == "darwin"
            else ["cp", "-a", "--reflink=auto", str(source), str(destination)]
        )
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            shutil.rmtree(destination, ignore_errors=True)
            return False
        if completed.returncode == 0 and destination.exists():
            return True
        shutil.rmtree(destination, ignore_errors=True)
        return False

    def _safe_path_part(self, value: str) -> str:
        safe = "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "-"
            for ch in str(value or "").strip()
        )
        return safe[:64] or "stage3"

    def _run_test_file(
        self,
        *,
        run_id: str = "",
        image_tag: str,
        repo_dir: Path,
        run_script_host_path: Path,
        test_file_path: str,
        target_selector: str | None = None,
        timeout_seconds: float,
    ) -> CommandResult:
        selector = shlex.quote(str(target_selector or test_file_path or "").strip())
        container_name = f"feature-factory-stage3-eval-{uuid.uuid4().hex}"
        shell_command = (
            "/workspace/run_script.sh "
            f"--action run --target-selector {selector} --out /tmp/run.json; "
            'rc=$?; chmod -R a+rwX /workspace/repo >/dev/null 2>&1 || true; '
            'if [ -s /tmp/run.json ]; then cat /tmp/run.json; fi; exit "$rc"'
        )
        create_command = [
            "docker",
            "create",
            "--name",
            container_name,
            "--label",
            f"{FEATURE_FACTORY_DOCKER_LABEL_MANAGED}=true",
            "--label",
            f"{FEATURE_FACTORY_DOCKER_LABEL_STAGE}=stage3",
            "--label",
            f"{FEATURE_FACTORY_DOCKER_LABEL_COMPONENT}={FEATURE_FACTORY_STAGE3_EVAL_COMPONENT}",
            "--label",
            f"{FEATURE_FACTORY_DOCKER_LABEL_RUN_ID}={str(run_id or '').strip()}",
            "--label",
            f"{FEATURE_FACTORY_DOCKER_LABEL_TEST_FILE}={test_file_path}",
            "--label",
            f"{FEATURE_FACTORY_DOCKER_LABEL_CREATED_AT}={datetime.now(UTC).isoformat().replace('+00:00', 'Z')}",
            "-v",
            f"{repo_dir}:/workspace/repo",
            "-v",
            f"{run_script_host_path}:/workspace/run_script.sh:ro",
            image_tag,
            "sh",
            "-lc",
            shell_command,
        ]
        deadline = time.monotonic() + timeout_seconds
        create_result = self._run_command(create_command, timeout_seconds=timeout_seconds)
        if create_result.returncode != 0 or create_result.timed_out:
            self._remove_container(container_name)
            return create_result
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            self._repair_container_repo_permissions(container_name)
            self._remove_container(container_name)
            _make_tree_container_writable(repo_dir)
            return CommandResult(
                args=["docker", "start", "-a", container_name],
                returncode=124,
                stdout="",
                stderr="stage3 test file docker container start exceeded timeout budget",
                timed_out=True,
            )

        try:
            return self._run_command(
                ["docker", "start", "-a", container_name],
                timeout_seconds=remaining_seconds,
            )
        finally:
            self._repair_container_repo_permissions(container_name)
            self._remove_container(container_name)
            _make_tree_container_writable(repo_dir)

    def _run_command(self, args: list[str], *, timeout_seconds: float) -> CommandResult:
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
            return CommandResult(
                args=args,
                returncode=completed.returncode,
                stdout=str(completed.stdout or ""),
                stderr=str(completed.stderr or ""),
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                args=args,
                returncode=124,
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or ""),
                timed_out=True,
            )

    def _remove_container(self, container_name: str) -> None:
        normalized = str(container_name or "").strip()
        if not normalized:
            return
        try:
            subprocess.run(
                ["docker", "rm", "-f", normalized],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return

    def _repair_container_repo_permissions(self, container_name: str) -> None:
        normalized = str(container_name or "").strip()
        if not normalized:
            return
        try:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "--user",
                    "root",
                    normalized,
                    "sh",
                    "-lc",
                    "chmod -R a+rwX /workspace/repo >/dev/null 2>&1 || true",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return


def _make_tree_container_writable(path: Path) -> None:
    try:
        _chmod_container_writable(path)
    except OSError:
        return
    for child in path.rglob("*"):
        if child.is_symlink():
            continue
        try:
            _chmod_container_writable(child)
        except OSError:
            continue


def _chmod_container_writable(path: Path) -> None:
    current_mode = stat.S_IMODE(path.stat().st_mode)
    if path.is_dir():
        desired_mode = current_mode | stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO
    else:
        desired_mode = (
            current_mode
            | stat.S_IRUSR
            | stat.S_IWUSR
            | stat.S_IRGRP
            | stat.S_IWGRP
            | stat.S_IROTH
            | stat.S_IWOTH
        )
    if desired_mode != current_mode:
        path.chmod(desired_mode)
