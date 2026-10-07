from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit


CHINA_TIMEZONES = frozenset(
    {
        "Asia/Shanghai",
        "Asia/Chongqing",
        "Asia/Harbin",
        "Asia/Urumqi",
        "Asia/Hong_Kong",
        "Asia/Macau",
    }
)
DEFAULT_DOCKER_IO_MIRROR = "docker.1ms.run"
DEFAULT_GHCR_IO_MIRROR = "ghcr.m.daocloud.io"
DEFAULT_GITHUB_PROXY_PREFIX = "https://ghfast.top/"
GITHUB_PROXYABLE_HOSTS = frozenset({"github.com", "www.github.com"})
LOCAL_IMAGE_PREFIXES = (
    "feature-factory/",
    "feature-factory-stage2:",
    "feature-factory-stage2/",
)
_FROM_RE = re.compile(r"^(?P<prefix>\s*FROM\s+)(?P<body>.*?)(?P<suffix>\s*(?:#.*)?)$", re.IGNORECASE)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _GitHubProxyQueue:
    def __init__(self) -> None:
        self._lock = Lock()
        self._configured: tuple[str, ...] = ()
        self._queue: deque[str] = deque()

    def ordered(self) -> tuple[str, ...]:
        configured = _configured_github_proxy_prefixes()
        with self._lock:
            self._sync_locked(configured)
            return tuple(self._queue)

    def current_and_ordered(self) -> tuple[str, tuple[str, ...]]:
        configured = _configured_github_proxy_prefixes()
        with self._lock:
            self._sync_locked(configured)
            return self._queue[0], tuple(self._queue)

    def move_to_back(self, failed_prefix: str) -> tuple[str, str]:
        configured = _configured_github_proxy_prefixes()
        with self._lock:
            self._sync_locked(configured)
            normalized = _normalize_github_proxy_prefix(failed_prefix)
            if normalized in self._queue:
                self._queue.remove(normalized)
                self._queue.append(normalized)
            return normalized, self._queue[0]

    def reset(self) -> None:
        with self._lock:
            self._configured = ()
            self._queue.clear()

    def _sync_locked(self, configured: tuple[str, ...]) -> None:
        if configured == self._configured and self._queue:
            return
        self._configured = configured
        self._queue = deque(configured)


_GITHUB_PROXY_QUEUE = _GitHubProxyQueue()


def env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is not None:
        return value
    return _dotenv_values().get(name)


def should_use_china_mirrors() -> bool:
    override = env_value("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS") or env_value(
        "FEATURE_FACTORY_USE_CHINA_MIRRORS"
    )
    if override is not None:
        return override.strip().lower() in {"1", "true", "yes", "on", "cn", "china"}

    local_timezone = _local_timezone_name()
    if local_timezone in CHINA_TIMEZONES:
        return True

    local_offset = datetime.now().astimezone().utcoffset()
    if local_offset != timedelta(hours=8):
        return False
    return any(name.upper() == "CST" for name in time_zone_names())


def should_use_china_docker_mirrors() -> bool:
    override = env_value("FEATURE_FACTORY_DOCKER_USE_CHINA_MIRRORS")
    if override is not None:
        return override.strip().lower() in {"1", "true", "yes", "on", "cn", "china"}
    return should_use_china_mirrors()


def docker_io_mirror_host() -> str:
    return _clean_host(env_value("FEATURE_FACTORY_DOCKER_IO_MIRROR") or DEFAULT_DOCKER_IO_MIRROR)


def ghcr_io_mirror_host() -> str:
    return _clean_host(env_value("FEATURE_FACTORY_GHCR_IO_MIRROR") or DEFAULT_GHCR_IO_MIRROR)


def github_proxy_prefixes() -> tuple[str, ...]:
    """Return the shared queue in its current failover order."""

    return _GITHUB_PROXY_QUEUE.ordered()


def github_proxy_prefix() -> str:
    """Return the current head of the process-wide GitHub proxy queue."""

    current, _ordered = _GITHUB_PROXY_QUEUE.current_and_ordered()
    return current


def github_proxy_retry_attempts(args: list[str] | tuple[str, ...], *, minimum: int) -> int:
    """Allow a proxied operation to try every configured proxy at least once."""

    _current, prefixes = _GITHUB_PROXY_QUEUE.current_and_ordered()
    if not any(_github_proxy_match(arg, prefixes=prefixes) for arg in args):
        return minimum
    return max(minimum, len(prefixes))


def refresh_github_proxy_urls(args: list[str]) -> tuple[str | None, str | None]:
    """Rewrite proxied GitHub URLs in ``args`` to the queue's current head."""

    current, prefixes = _GITHUB_PROXY_QUEUE.current_and_ordered()
    previous: str | None = None
    for index, value in enumerate(args):
        match = _github_proxy_match(value, prefixes=prefixes)
        if match is None:
            continue
        matched, offset = match
        previous = previous or matched
        if matched != current:
            args[index] = (
                f"{value[:offset]}{current}{value[offset + len(matched):]}"
            )
    return previous, current if previous is not None else None


def rotate_failed_github_proxy_urls(args: list[str]) -> tuple[str | None, str | None]:
    """Move the proxy used by ``args`` to the tail and select the new head."""

    _current, prefixes = _GITHUB_PROXY_QUEUE.current_and_ordered()
    failed_match = next(
        (
            match
            for value in args
            if (match := _github_proxy_match(value, prefixes=prefixes)) is not None
        ),
        None,
    )
    if failed_match is None:
        return None, None
    failed, _offset = failed_match
    failed, next_prefix = _GITHUB_PROXY_QUEUE.move_to_back(failed)
    for index, value in enumerate(args):
        match = _github_proxy_match(value, prefixes=prefixes)
        if match is None:
            continue
        matched, offset = match
        if matched == failed and matched != next_prefix:
            args[index] = (
                f"{value[:offset]}{next_prefix}{value[offset + len(matched):]}"
            )
    return failed, next_prefix


def reset_github_proxy_queue() -> None:
    """Reload the proxy queue from configuration on its next use."""

    _GITHUB_PROXY_QUEUE.reset()


def temporary_git_remote_url(url: str, *, enabled: bool | None = None) -> str:
    remote_url = str(url or "").strip()
    if not remote_url:
        return remote_url
    if enabled is None:
        enabled = should_use_china_mirrors()
    if not enabled:
        return remote_url
    proxy_prefix = github_proxy_prefix()
    if not proxy_prefix:
        return remote_url
    normalized_prefix = proxy_prefix if proxy_prefix.endswith("/") else f"{proxy_prefix}/"
    if remote_url.startswith(normalized_prefix):
        return remote_url
    parsed = urlsplit(remote_url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return remote_url
    if (parsed.hostname or "").lower() not in GITHUB_PROXYABLE_HOSTS:
        return remote_url
    return f"{normalized_prefix}{remote_url}"


def _configured_github_proxy_prefixes() -> tuple[str, ...]:
    raw_value = str(
        env_value("FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX")
        or DEFAULT_GITHUB_PROXY_PREFIX
    ).strip()
    candidates: list[object]
    try:
        decoded = json.loads(raw_value)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, list):
        candidates = decoded
    elif isinstance(decoded, str):
        candidates = [decoded]
    else:
        candidates = re.split(r"[,\n]", raw_value)

    prefixes: list[str] = []
    for candidate in candidates:
        normalized = _normalize_github_proxy_prefix(str(candidate or ""))
        if normalized and normalized not in prefixes:
            prefixes.append(normalized)
    return tuple(prefixes or [_normalize_github_proxy_prefix(DEFAULT_GITHUB_PROXY_PREFIX)])


def _normalize_github_proxy_prefix(value: str) -> str:
    stripped = str(value or "").strip()
    if not stripped:
        return ""
    return stripped if stripped.endswith("/") else f"{stripped}/"


def _github_proxy_match(
    url: str,
    *,
    prefixes: tuple[str, ...],
) -> tuple[str, int] | None:
    value = str(url or "")
    offset = 0
    for assignment_prefix in ("lfs.url=", "remote.origin.url="):
        if value.startswith(assignment_prefix):
            offset = len(assignment_prefix)
            break
    candidate = value[offset:]
    for prefix in sorted(prefixes, key=len, reverse=True):
        if not candidate.startswith(prefix):
            continue
        upstream = candidate[len(prefix) :]
        parsed = urlsplit(upstream)
        if (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and (parsed.hostname or "").lower() in GITHUB_PROXYABLE_HOSTS
        ):
            return prefix, offset
    return None


def mirror_docker_image_ref(image_ref: str, *, enabled: bool | None = None) -> str:
    reference = str(image_ref or "").strip()
    if not reference:
        return reference
    if enabled is None:
        enabled = should_use_china_docker_mirrors()
    if not enabled:
        return reference
    if _is_unmirrorable_reference(reference):
        return reference

    registry, remainder = _split_registry(reference)
    if registry in {"docker.io", "index.docker.io"}:
        return f"{docker_io_mirror_host()}/{remainder}"
    if registry == "ghcr.io":
        return f"{ghcr_io_mirror_host()}/{remainder}"
    if registry:
        return reference

    if "/" not in reference:
        return f"{docker_io_mirror_host()}/library/{reference}"
    return f"{docker_io_mirror_host()}/{reference}"


def rewrite_dockerfile_from_images(text: str, *, enabled: bool | None = None) -> str:
    if enabled is None:
        enabled = should_use_china_docker_mirrors()
    if not enabled:
        return text

    stage_aliases: set[str] = set()
    rewritten_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        line_body = line[:-1] if line.endswith("\n") else line
        newline = "\n" if line.endswith("\n") else ""
        rewritten_body, alias = _rewrite_dockerfile_line(line_body, stage_aliases=stage_aliases)
        rewritten_lines.append(f"{rewritten_body}{newline}")
        if alias:
            stage_aliases.add(alias)
    return "".join(rewritten_lines)


@contextmanager
def dockerfile_with_mirrored_from_images(
    dockerfile_path: Path,
    *,
    enabled: bool | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> Iterator[Path]:
    resolved_path = dockerfile_path.resolve()
    if not resolved_path.is_file():
        yield resolved_path
        return
    text = resolved_path.read_text(encoding="utf-8")
    rewritten = rewrite_dockerfile_from_images(text, enabled=enabled)
    if rewritten == text:
        yield resolved_path
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="feature-factory-dockerfile-"))
    temp_path = temp_dir / resolved_path.name
    try:
        temp_path.write_text(rewritten, encoding="utf-8")
        if log_callback is not None:
            log_callback(
                "Using CN Docker image mirrors for public base images "
                f"(docker.io -> {docker_io_mirror_host()}, ghcr.io -> {ghcr_io_mirror_host()})"
            )
        yield temp_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _rewrite_dockerfile_line(line: str, *, stage_aliases: set[str]) -> tuple[str, str | None]:
    return _rewrite_from_line(line, stage_aliases=stage_aliases)


def _rewrite_from_line(line: str, *, stage_aliases: set[str]) -> tuple[str, str | None]:
    match = _FROM_RE.match(line)
    if not match:
        return line, None

    body = match.group("body")
    tokens = body.split()
    if not tokens:
        return line, None

    image_token_index = 0
    while image_token_index < len(tokens) and tokens[image_token_index].startswith("--"):
        image_token_index += 1
    if image_token_index >= len(tokens):
        return line, None

    image_ref = tokens[image_token_index]
    alias = _from_alias(tokens)
    if image_ref not in stage_aliases:
        tokens[image_token_index] = mirror_docker_image_ref(image_ref, enabled=True)
    return f"{match.group('prefix')}{' '.join(tokens)}{match.group('suffix')}", alias


def _from_alias(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens[:-1]):
        if token.lower() == "as":
            return tokens[index + 1]
    return None


def _is_unmirrorable_reference(reference: str) -> bool:
    if reference == "scratch":
        return True
    if reference.startswith("${") or reference.startswith("$"):
        return True
    if any(reference.startswith(prefix) for prefix in LOCAL_IMAGE_PREFIXES):
        return True
    if reference.startswith((docker_io_mirror_host() + "/", ghcr_io_mirror_host() + "/")):
        return True
    return False


def _split_registry(reference: str) -> tuple[str, str]:
    first, separator, remainder = reference.partition("/")
    if not separator:
        return "", reference
    if "." in first or ":" in first or first == "localhost":
        return first.lower(), remainder
    return "", reference


def _clean_host(value: str) -> str:
    return value.strip().removeprefix("https://").removeprefix("http://").rstrip("/")


def _local_timezone_name() -> str:
    tz_env = os.getenv("TZ")
    if tz_env:
        return tz_env.strip().lstrip(":")
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        local_tz = datetime.now().astimezone().tzinfo
        if isinstance(local_tz, ZoneInfo):
            return str(local_tz.key)
    except Exception:
        return ""
    return ""


def time_zone_names() -> tuple[str, ...]:
    import time

    return tuple(time.tzname)


@lru_cache(maxsize=1)
def _dotenv_values() -> dict[str, str]:
    dotenv_path = _PROJECT_ROOT / ".env"
    if not dotenv_path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        parsed = _parse_dotenv_line(line)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
    return values


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].lstrip()
    key, separator, value = stripped.partition("=")
    if not separator:
        return None
    key = key.strip()
    if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value
