from __future__ import annotations

import os
import urllib.parse
from collections.abc import Callable


LOCALHOST_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
CONTAINER_HOST_GATEWAY_ENV = "FEATURE_FACTORY_CONTAINER_HOST_GATEWAY"
DEFAULT_CONTAINER_HOST_GATEWAY = "172.17.0.1"


def rewrite_local_llm_base_url_for_container(
    base_url: str | None,
    *,
    get_env: Callable[[str], str | None] = os.environ.get,
) -> str | None:
    """Rewrite host-loopback LLM URLs so Docker bridge containers can reach them."""
    if base_url is None:
        return None
    raw = str(base_url).strip()
    if not raw:
        return raw
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        return raw
    if (parsed.hostname or "").lower() not in LOCALHOST_NAMES:
        return raw
    replacement_host = (
        str(get_env(CONTAINER_HOST_GATEWAY_ENV) or "").strip()
        or DEFAULT_CONTAINER_HOST_GATEWAY
    )
    return urllib.parse.urlunsplit(
        parsed._replace(netloc=_replace_netloc_host(parsed, replacement_host))
    )


def _replace_netloc_host(parsed: urllib.parse.SplitResult, host: str) -> str:
    userinfo = ""
    if parsed.username:
        userinfo = urllib.parse.quote(parsed.username, safe="")
        if parsed.password is not None:
            userinfo += f":{urllib.parse.quote(parsed.password, safe='')}"
        userinfo += "@"
    host_part = f"[{host}]" if ":" in host and not host.startswith("[") else host
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{userinfo}{host_part}{port}"