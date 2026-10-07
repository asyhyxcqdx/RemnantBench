from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Any, Callable, MutableMapping

from feature_factory.llm_base_url import rewrite_local_llm_base_url_for_container


DEPLOYMENT_LLM_MODEL_ENV = "FEATURE_FACTORY_DEPLOYMENT_LLM_MODEL"
HUAWEI_LLM_MODEL_ENV = "FEATURE_FACTORY_HUAWEI_LLM_MODEL"
HUAWEI_LLM_BASE_URL_ENV = "FEATURE_FACTORY_HUAWEI_LLM_BASE_URL"
HUAWEI_LLM_API_KEY_ENV = "FEATURE_FACTORY_HUAWEI_LLM_API_KEY"
OPENHANDS_LLM_SSL_VERIFY_ENV = "FEATURE_FACTORY_LLM_SSL_VERIFY"
_LEGACY_LITELLM_SSL_VERIFY_ENV = "SSL_VERIFY"
_LLM_MODEL_ENV_NAMES = (
    "LLM_MODEL",
    "LLM_MODEL_NAME",
    "FEATURE_FACTORY_LLM_MODEL",
    "FEATURE_FACTORY_LLM_MODEL_NAME",
    HUAWEI_LLM_MODEL_ENV,
)


@dataclass(frozen=True, slots=True)
class OpenHandsLLMConfig:
    model: str
    api_key: str | None
    base_url: str | None
    optional_kwargs: dict[str, Any]

    def replace(self, **kwargs: Any) -> "OpenHandsLLMConfig":
        return replace(self, **kwargs)


def build_openhands_llm_config(*, usage_id: str) -> OpenHandsLLMConfig:
    raw_model = _first_env(*_LLM_MODEL_ENV_NAMES)
    if not raw_model:
        raise RuntimeError("LLM_MODEL is required for OpenHands bridge")

    optional_kwargs: dict[str, Any] = {}
    ssl_verify = _env_ssl_verify(
        (OPENHANDS_LLM_SSL_VERIFY_ENV, _LEGACY_LITELLM_SSL_VERIFY_ENV)
    )
    if ssl_verify is not None:
        optional_kwargs["ssl_verify"] = ssl_verify
    timeout = _env_int(
        ("LLM_TIMEOUT", "FEATURE_FACTORY_OPENHANDS_LLM_TIMEOUT"),
        default=300,
    )
    if timeout is not None:
        optional_kwargs["timeout"] = timeout
    native_tool_calling = _env_bool(
        (
            "LLM_NATIVE_TOOL_CALLING",
            "FEATURE_FACTORY_OPENHANDS_NATIVE_TOOL_CALLING",
            "FEATURE_FACTORY_LLM_NATIVE_TOOL_CALLING",
        )
    )
    if native_tool_calling is not None:
        optional_kwargs["native_tool_calling"] = native_tool_calling
    extra_headers = _extra_headers_from_env()
    if extra_headers:
        optional_kwargs["extra_headers"] = extra_headers

    base_url = _first_env(
        "LLM_BASE_URL",
        "LLM_API_BASE_URL",
        "LLM_API_BASE",
        "OPENAI_BASE_URL",
        "FEATURE_FACTORY_LLM_BASE_URL",
        "FEATURE_FACTORY_LLM_API_BASE_URL",
        HUAWEI_LLM_BASE_URL_ENV,
    )

    config = OpenHandsLLMConfig(
        model=raw_model,
        api_key=_first_env(
            "LLM_API_KEY",
            "OPENAI_API_KEY",
            "FEATURE_FACTORY_LLM_API_KEY",
            HUAWEI_LLM_API_KEY_ENV,
        ),
        base_url=rewrite_local_llm_base_url_for_container(base_url),
        optional_kwargs=optional_kwargs,
    )
    from feature_factory.llm_adapters import apply_openhands_llm_adapters

    return apply_openhands_llm_adapters(config, usage_id=usage_id)


def openhands_llm_session_id(llm: Any) -> str | None:
    from feature_factory.llm_adapters import openhands_llm_adapter_session_id

    return openhands_llm_adapter_session_id(llm)


def release_openhands_llm_resources(
    *,
    llm: Any,
    on_error: Callable[[str], None] | None = None,
) -> None:
    from feature_factory.llm_adapters import release_openhands_llm_adapter_resources

    release_openhands_llm_adapter_resources(llm=llm, on_error=on_error)


def preserve_deployment_llm_model_env(
    env: MutableMapping[str, str],
    *,
    deployment_model: str | None = None,
) -> None:
    if env.get(DEPLOYMENT_LLM_MODEL_ENV):
        return
    model = (deployment_model or "").strip() or _first_mapping_env(
        env, HUAWEI_LLM_MODEL_ENV
    )
    if model:
        env[DEPLOYMENT_LLM_MODEL_ENV] = model


def set_openhands_llm_ssl_verify_env(
    env: MutableMapping[str, str],
    *,
    verify: bool | os.PathLike[str] | str,
) -> None:
    """Pass TLS verification through the serialized OpenHands LLM config."""
    if isinstance(verify, bool):
        value = "true" if verify else "false"
    else:
        value = os.fspath(verify)
    env[OPENHANDS_LLM_SSL_VERIFY_ENV] = value


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _first_mapping_env(env: MutableMapping[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _extra_headers_from_env() -> dict[str, str]:
    raw = _first_env("LLM_EXTRA_HEADERS", "FEATURE_FACTORY_OPENHANDS_LLM_EXTRA_HEADERS")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LLM_EXTRA_HEADERS must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM_EXTRA_HEADERS must be a JSON object")
    return {str(key): str(value) for key, value in parsed.items()}


def _env_int(names: tuple[str, ...], *, default: int | None) -> int | None:
    raw = _first_env(*names)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{names[0]} must be an integer") from exc


def _env_bool(names: tuple[str, ...]) -> bool | None:
    raw = _first_env(*names)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _env_ssl_verify(names: tuple[str, ...]) -> bool | str | None:
    raw = _first_env(*names)
    if raw is None:
        return None
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return raw