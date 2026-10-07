import threading
import time

from feature_factory.config import Settings
from feature_factory.stage1.token_scheduler import GitHubTokenScheduler


def test_token_scheduler_switches_to_another_token_when_one_cools_down() -> None:
    scheduler = GitHubTokenScheduler(Settings(database_url="sqlite://"), runtime_tokens=["token-a", "token-b"])

    first = scheduler.acquire()
    assert first.token in {"token-a", "token-b"}
    scheduler.release(first, cooldown_seconds=5)

    second = scheduler.acquire()
    assert second.token in {"token-a", "token-b"}
    assert second.token != first.token
    scheduler.release(second)


def test_token_scheduler_enforces_global_request_cap() -> None:
    scheduler = GitHubTokenScheduler(
        Settings(database_url="sqlite://", github_max_concurrent_requests=1),
        runtime_tokens=["token-a", "token-b"],
    )

    first = scheduler.acquire()
    acquired_second = threading.Event()
    second_holder: dict[str, object] = {}

    def acquire_second() -> None:
        second = scheduler.acquire()
        second_holder["lease"] = second
        acquired_second.set()

    thread = threading.Thread(target=acquire_second)
    thread.start()
    try:
        time.sleep(0.1)
        assert acquired_second.is_set() is False

        scheduler.release(first)
        assert acquired_second.wait(timeout=1.0) is True
        second = second_holder["lease"]
        scheduler.release(second)
    finally:
        thread.join(timeout=1.0)
