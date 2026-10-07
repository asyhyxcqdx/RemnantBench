from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Condition

from feature_factory.config import Settings


@dataclass(frozen=True)
class TokenLease:
    key: str
    token: str | None


@dataclass
class _TokenState:
    key: str
    token: str | None
    available_at: float = 0.0
    in_flight: int = 0
    last_used_at: float = 0.0


class GitHubTokenScheduler:
    def __init__(self, settings: Settings, runtime_tokens: list[str] | None = None) -> None:
        self.settings = settings
        self._condition = Condition()
        self._global_in_flight = 0
        self._static_tokens = self._normalize_tokens(
            [settings.github_token.get_secret_value()] if settings.github_token else []
        )
        self._runtime_tokens: list[str] = []
        self._tokens: dict[str, _TokenState] = {}
        self.set_runtime_tokens(runtime_tokens or [])

    def set_runtime_tokens(self, tokens: list[str]) -> None:
        normalized_runtime = self._normalize_tokens(tokens)
        merged = self._normalize_tokens([*self._static_tokens, *normalized_runtime])
        if not merged:
            merged = []

        with self._condition:
            previous = self._tokens
            next_tokens: dict[str, _TokenState] = {}
            for token in merged:
                key = self._token_key(token)
                state = previous.get(key) or _TokenState(key=key, token=token)
                state.token = token
                next_tokens[key] = state
            if not next_tokens:
                next_tokens["__anonymous__"] = previous.get("__anonymous__") or _TokenState(
                    key="__anonymous__",
                    token=None,
                )
            self._runtime_tokens = normalized_runtime
            self._tokens = next_tokens
            self._condition.notify_all()

    def get_runtime_token_count(self) -> int:
        with self._condition:
            return len(self._runtime_tokens)

    def get_total_token_count(self) -> int:
        with self._condition:
            return sum(1 for state in self._tokens.values() if state.token)

    def acquire(self) -> TokenLease:
        with self._condition:
            while True:
                now = time.time()
                if self._global_in_flight >= self.settings.github_max_concurrent_requests:
                    self._condition.wait(timeout=0.05)
                    continue
                candidates = [state for state in self._tokens.values() if state.available_at <= now]
                if candidates:
                    chosen = min(candidates, key=lambda state: (state.in_flight, state.last_used_at, state.key))
                    chosen.in_flight += 1
                    chosen.last_used_at = now
                    self._global_in_flight += 1
                    return TokenLease(key=chosen.key, token=chosen.token)
                next_ready = min(state.available_at for state in self._tokens.values())
                self._condition.wait(timeout=max(next_ready - now, 0.05))

    def release(self, lease: TokenLease, *, cooldown_seconds: float = 0.0) -> None:
        with self._condition:
            state = self._tokens.get(lease.key)
            self._global_in_flight = max(self._global_in_flight - 1, 0)
            if state is not None:
                state.in_flight = max(state.in_flight - 1, 0)
                if cooldown_seconds > 0:
                    state.available_at = max(state.available_at, time.time() + cooldown_seconds)
            self._condition.notify_all()

    def build_headers(self, base_headers: dict[str, str], lease: TokenLease) -> dict[str, str]:
        headers = dict(base_headers)
        if lease.token:
            headers["Authorization"] = f"Bearer {lease.token}"
        return headers

    def cooldown_for_response(self, headers: dict[str, str]) -> float:
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                return max(float(retry_after), 1.0)
            except ValueError:
                pass

        reset_at = headers.get("x-ratelimit-reset")
        if reset_at:
            try:
                return max(float(reset_at) - time.time() + 1.0, 1.0)
            except ValueError:
                pass

        return max(self.settings.github_rate_limit_wait_seconds, 1.0)

    def _normalize_tokens(self, tokens: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            value = token.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return deduped

    def _token_key(self, token: str | None) -> str:
        if token is None:
            return "__anonymous__"
        return token
