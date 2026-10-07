from __future__ import annotations

from collections.abc import Callable, MutableSet
from pathlib import Path


def process_pending_validation_requests(
    *,
    request_dir: Path,
    processed_request_ids: MutableSet[str],
    handle_request: Callable[..., None],
    handle_error: Callable[..., None],
    handle_error_failure: Callable[..., bool] | None = None,
) -> bool:
    handled = False
    for request_path in sorted(request_dir.glob("*.json")):
        request_id = request_path.stem
        if request_id in processed_request_ids:
            continue
        try:
            handle_request(request_id=request_id, request_path=request_path)
        except Exception as exc:
            try:
                handle_error(request_id=request_id, request_path=request_path, exc=exc)
            except Exception as error_exc:
                recovered = False
                if handle_error_failure is not None:
                    recovered = bool(
                        handle_error_failure(
                            request_id=request_id,
                            request_path=request_path,
                            request_exc=exc,
                            error_handler_exc=error_exc,
                        )
                    )
                if recovered:
                    processed_request_ids.add(request_id)
                    handled = True
                continue
            processed_request_ids.add(request_id)
        else:
            processed_request_ids.add(request_id)
        handled = True
    return handled
