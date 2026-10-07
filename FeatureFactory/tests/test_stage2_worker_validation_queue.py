from __future__ import annotations

from pathlib import Path

from feature_factory.stage2.worker_validation_queue import process_pending_validation_requests


def test_process_pending_validation_requests_returns_false_for_empty_queue(tmp_path) -> None:
    handled_request_ids: list[str] = []
    error_request_ids: list[str] = []

    handled = process_pending_validation_requests(
        request_dir=tmp_path,
        processed_request_ids=set(),
        handle_request=lambda request_id, request_path: handled_request_ids.append(request_id),
        handle_error=lambda request_id, request_path, exc: error_request_ids.append(request_id),
    )

    assert handled is False
    assert handled_request_ids == []
    assert error_request_ids == []


def test_process_pending_validation_requests_continues_after_request_error(tmp_path) -> None:
    processed_request_ids: set[str] = set()
    handled_request_ids: list[str] = []
    error_payloads: list[tuple[str, str]] = []
    request_a = tmp_path / "a.json"
    request_b = tmp_path / "b.json"
    request_a.write_text("{}\n", encoding="utf-8")
    request_b.write_text("{}\n", encoding="utf-8")

    def handle_request(request_id: str, request_path: Path) -> None:
        handled_request_ids.append(request_id)
        if request_id == "a":
            raise RuntimeError("boom")

    def handle_error(request_id: str, request_path: Path, exc: Exception) -> None:
        error_payloads.append((request_id, str(exc)))

    handled = process_pending_validation_requests(
        request_dir=tmp_path,
        processed_request_ids=processed_request_ids,
        handle_request=handle_request,
        handle_error=handle_error,
    )

    assert handled is True
    assert handled_request_ids == ["a", "b"]
    assert error_payloads == [("a", "boom")]
    assert processed_request_ids == {"a", "b"}


def test_process_pending_validation_requests_retries_when_error_handler_fails(tmp_path) -> None:
    processed_request_ids: set[str] = set()
    handled_request_ids: list[str] = []
    error_payloads: list[tuple[str, str]] = []
    error_handler_failures: list[tuple[str, str, str]] = []
    request_a = tmp_path / "a.json"
    request_a.write_text("{}\n", encoding="utf-8")

    def handle_request(request_id: str, request_path: Path) -> None:
        handled_request_ids.append(request_id)
        raise RuntimeError("boom")

    def handle_error(request_id: str, request_path: Path, exc: Exception) -> None:
        error_payloads.append((request_id, str(exc)))
        if len(error_payloads) == 1:
            raise RuntimeError("error handler boom")

    handled = process_pending_validation_requests(
        request_dir=tmp_path,
        processed_request_ids=processed_request_ids,
        handle_request=handle_request,
        handle_error=handle_error,
        handle_error_failure=lambda request_id, request_path, request_exc, error_handler_exc: error_handler_failures.append(
            (request_id, str(request_exc), str(error_handler_exc))
        ),
    )

    assert handled is False
    assert handled_request_ids == ["a"]
    assert error_payloads == [("a", "boom")]
    assert error_handler_failures == [("a", "boom", "error handler boom")]
    assert processed_request_ids == set()

    handled = process_pending_validation_requests(
        request_dir=tmp_path,
        processed_request_ids=processed_request_ids,
        handle_request=handle_request,
        handle_error=handle_error,
    )

    assert handled is True
    assert handled_request_ids == ["a", "a"]
    assert error_payloads == [("a", "boom"), ("a", "boom")]
    assert processed_request_ids == {"a"}


def test_process_pending_validation_requests_marks_processed_when_error_failure_recovers(
    tmp_path,
) -> None:
    processed_request_ids: set[str] = set()
    request_a = tmp_path / "a.json"
    request_a.write_text("{}\n", encoding="utf-8")
    recovery_calls: list[tuple[str, str, str]] = []

    def handle_request(request_id: str, request_path: Path) -> None:
        raise RuntimeError("boom")

    def handle_error(request_id: str, request_path: Path, exc: Exception) -> None:
        raise RuntimeError("error handler boom")

    handled = process_pending_validation_requests(
        request_dir=tmp_path,
        processed_request_ids=processed_request_ids,
        handle_request=handle_request,
        handle_error=handle_error,
        handle_error_failure=lambda request_id, request_path, request_exc, error_handler_exc: (
            recovery_calls.append((request_id, str(request_exc), str(error_handler_exc)))
            or True
        ),
    )

    assert handled is True
    assert recovery_calls == [("a", "boom", "error handler boom")]
    assert processed_request_ids == {"a"}


def test_process_pending_validation_requests_supports_keyword_only_handlers(tmp_path) -> None:
    processed_request_ids: set[str] = set()
    request_a = tmp_path / "a.json"
    request_a.write_text("{}\n", encoding="utf-8")
    handled_payloads: list[tuple[str, str]] = []

    def handle_request(*, request_id: str, request_path: Path) -> None:
        handled_payloads.append((request_id, request_path.name))

    def handle_error(*, request_id: str, request_path: Path, exc: Exception) -> None:  # noqa: ARG001
        raise AssertionError("handle_error should not be called")

    handled = process_pending_validation_requests(
        request_dir=tmp_path,
        processed_request_ids=processed_request_ids,
        handle_request=handle_request,
        handle_error=handle_error,
    )

    assert handled is True
    assert handled_payloads == [("a", "a.json")]
    assert processed_request_ids == {"a"}
