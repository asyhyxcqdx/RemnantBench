from __future__ import annotations

import os
import ssl
import urllib.parse
import urllib.request
import uuid
from typing import TYPE_CHECKING, Any, Callable

from feature_factory.openhands_llm import DEPLOYMENT_LLM_MODEL_ENV, HUAWEI_LLM_MODEL_ENV

if TYPE_CHECKING:
    from feature_factory.openhands_llm import OpenHandsLLMConfig

HUAWEI_LLM_SESSION_HEADER = "X-Session-Id"
HUAWEI_LLM_ENABLE_REASONING_ENV = "FEATURE_FACTORY_HUAWEI_LLM_ENABLE_REASONING"
HUAWEI_LLM_NO_AUTH_API_KEY = "not-required"


def adapt_openhands_llm_config(
    config: "OpenHandsLLMConfig",
    *,
    usage_id: str,
) -> "OpenHandsLLMConfig":
    if not _adapter_enabled(model=config.model, base_url=config.base_url):
        return config

    provider = _explicit_provider() or "openai"
    optional_kwargs = dict(config.optional_kwargs)
    optional_kwargs.setdefault("native_tool_calling", True)
    optional_kwargs.setdefault(
        "num_retries",
        _env_int(
            (
                "FEATURE_FACTORY_HUAWEI_LLM_NUM_RETRIES",
                "LLM_NUM_RETRIES",
            ),
            default=10,
        ),
    )
    optional_kwargs.setdefault(
        "temperature",
        _env_float(
            (
                "FEATURE_FACTORY_HUAWEI_LLM_TEMPERATURE",
                "LLM_TEMPERATURE",
            ),
            default=0.7,
        ),
    )
    optional_kwargs.setdefault(
        "top_p",
        _env_float(
            (
                "FEATURE_FACTORY_HUAWEI_LLM_TOP_P",
                "LLM_TOP_P",
            ),
            default=0.95,
        ),
    )
    optional_kwargs.setdefault(
        "max_output_tokens",
        _env_int(
            (
                "FEATURE_FACTORY_HUAWEI_LLM_MAX_OUTPUT_TOKENS",
                "LLM_MAX_OUTPUT_TOKENS",
            ),
            default=16384,
        ),
    )
    headers = dict(optional_kwargs.get("extra_headers") or {})
    session_id = _session_id_for_openhands_run(usage_id=usage_id)
    if session_id:
        headers[HUAWEI_LLM_SESSION_HEADER] = session_id
    if headers:
        optional_kwargs["extra_headers"] = headers

    adapted_model = _with_litellm_provider_prefix(config.model, provider)
    if _reasoning_enabled():
        _enable_openhands_reasoning_compatibility(adapted_model)

    return config.replace(
        model=adapted_model,
        api_key=config.api_key or HUAWEI_LLM_NO_AUTH_API_KEY,
        optional_kwargs=optional_kwargs,
    )


def openhands_session_id(llm: Any) -> str | None:
    headers = getattr(llm, "extra_headers", None)
    if not isinstance(headers, dict):
        return None
    value = str(headers.get(HUAWEI_LLM_SESSION_HEADER) or "").strip()
    return value or None


def release_openhands_session(
    *,
    llm: Any,
    on_error: Callable[[str], None] | None = None,
) -> None:
    session_id = openhands_session_id(llm)
    api_base = str(getattr(llm, "base_url", "") or "")
    if not session_id or not api_base:
        return
    url = session_release_url(api_base=api_base, session_id=session_id)
    req = urllib.request.Request(url, method="DELETE")
    ssl_context = _ssl_context_for_verify(getattr(llm, "ssl_verify", None))
    try:
        with urllib.request.urlopen(req, timeout=10, context=ssl_context) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001
        if on_error is not None:
            on_error(str(exc))


def _ssl_context_for_verify(verify: Any) -> ssl.SSLContext | None:
    if verify is False:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    if isinstance(verify, (str, os.PathLike)):
        return ssl.create_default_context(cafile=os.fspath(verify))
    return None


def session_release_url(*, api_base: str, session_id: str) -> str:
    parsed = urllib.parse.urlsplit(api_base.strip())
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    path = f"{path.rstrip('/')}/session_release"
    query = urllib.parse.urlencode({"session_id": session_id})
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, ""))


def _enable_openhands_reasoning_compatibility(model: str) -> None:
    _enable_send_reasoning_content_model(_model_key(model))
    _enable_reasoning_field_aliases()


def _enable_send_reasoning_content_model(model: str) -> None:
    try:
        from openhands.sdk.llm.utils import model_features
    except Exception:  # noqa: BLE001
        return

    configured = getattr(model_features, "SEND_REASONING_CONTENT_MODELS", None)
    if not isinstance(configured, list):
        return

    key = model.strip()
    existing = {str(item).strip().lower() for item in configured}
    if key and key.lower() not in existing:
        configured.append(key)


def _reasoning_alias_value(message: Any) -> Any:
    for name in ("reasoning_content", "reasoning"):
        value = getattr(message, name, None)
        if value not in (None, ""):
            return value

    for name in ("provider_specific_fields", "model_extra"):
        value = getattr(message, name, None)
        if not isinstance(value, dict):
            continue
        for key in ("reasoning_content", "reasoning"):
            alias_value = value.get(key)
            if alias_value not in (None, ""):
                return alias_value

    return None


def _set_reasoning_aliases(message: Any, reasoning: Any) -> None:
    if reasoning in (None, ""):
        return

    for name in ("reasoning_content", "reasoning"):
        if getattr(message, name, None) not in (None, ""):
            continue
        try:
            setattr(message, name, reasoning)
        except Exception:  # noqa: BLE001
            pass

    provider_specific_fields = getattr(message, "provider_specific_fields", None)
    if not isinstance(provider_specific_fields, dict):
        provider_specific_fields = {}
        try:
            setattr(message, "provider_specific_fields", provider_specific_fields)
        except Exception:  # noqa: BLE001
            return
    provider_specific_fields.setdefault("reasoning_content", reasoning)
    provider_specific_fields.setdefault("reasoning", reasoning)


def _enable_reasoning_field_aliases() -> None:
    try:
        from openhands.sdk.llm.message import Message
    except Exception:  # noqa: BLE001
        return

    if getattr(Message, "_feature_factory_reasoning_field_aliases_enabled", False):
        return

    original_to_chat_dict = Message.to_chat_dict
    original_from_llm_chat_message = Message.from_llm_chat_message.__func__

    def to_chat_dict_with_reasoning_aliases(self: Any, *args: Any, **kwargs: Any) -> dict:
        message_dict = original_to_chat_dict(self, *args, **kwargs)
        reasoning_content = getattr(self, "reasoning_content", None)
        if kwargs.get("send_reasoning_content") and reasoning_content:
            message_dict["reasoning_content"] = reasoning_content
            message_dict["reasoning"] = reasoning_content
        return message_dict

    @classmethod
    def from_llm_chat_message_with_reasoning_aliases(cls: type, message: Any) -> Any:
        reasoning = _reasoning_alias_value(message)
        llm_message = original_from_llm_chat_message(cls, message)
        if reasoning not in (None, "") and not llm_message.reasoning_content:
            return llm_message.model_copy(update={"reasoning_content": reasoning})
        return llm_message

    Message.to_chat_dict = to_chat_dict_with_reasoning_aliases
    Message.from_llm_chat_message = from_llm_chat_message_with_reasoning_aliases
    Message._feature_factory_reasoning_field_aliases_enabled = True
    _enable_non_native_reasoning_field_aliases()


def _enable_non_native_reasoning_field_aliases() -> None:
    try:
        from openhands.sdk.llm.mixins.non_native_fc import NonNativeToolCallingMixin
    except Exception:  # noqa: BLE001
        return

    if getattr(
        NonNativeToolCallingMixin,
        "_feature_factory_reasoning_field_aliases_enabled",
        False,
    ):
        return

    original_post_response_prompt_mock = (
        NonNativeToolCallingMixin.post_response_prompt_mock
    )

    def post_response_prompt_mock_with_reasoning_aliases(
        self: Any,
        resp: Any,
        nonfncall_msgs: list[dict],
        tools: list[Any],
    ) -> Any:
        reasoning = None
        choices = getattr(resp, "choices", None)
        if choices:
            reasoning = _reasoning_alias_value(choices[0].message)

        patched_resp = original_post_response_prompt_mock(
            self,
            resp,
            nonfncall_msgs,
            tools,
        )
        if reasoning not in (None, "") and getattr(patched_resp, "choices", None):
            _set_reasoning_aliases(patched_resp.choices[0].message, reasoning)
        return patched_resp

    NonNativeToolCallingMixin.post_response_prompt_mock = (
        post_response_prompt_mock_with_reasoning_aliases
    )
    NonNativeToolCallingMixin._feature_factory_reasoning_field_aliases_enabled = True


def _adapter_enabled(*, model: str, base_url: str | None) -> bool:
    if not base_url:
        return False
    deployment_model = _first_env(DEPLOYMENT_LLM_MODEL_ENV, HUAWEI_LLM_MODEL_ENV)
    if not deployment_model:
        return False
    return _model_key(model) == _model_key(deployment_model)


def _explicit_provider() -> str | None:
    provider = _first_env(
        "FEATURE_FACTORY_HUAWEI_LLM_LITELLM_PROVIDER",
        "LLM_CUSTOM_LLM_PROVIDER",
        "LLM_CUSTOM_PROVIDER",
    )
    return provider.strip().lower() if provider else None


def _with_litellm_provider_prefix(model: str, provider: str | None) -> str:
    normalized = model.strip()
    if not provider or "/" in normalized:
        return normalized
    return f"{provider}/{normalized}"


def _model_key(model: str) -> str:
    normalized = model.strip().lower()
    for provider in _litellm_provider_prefixes():
        prefix = f"{provider}/"
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def _litellm_provider_prefixes() -> set[str]:
    providers = {"openai"}
    explicit = _explicit_provider()
    if explicit:
        providers.add(explicit)
    return providers


def _session_id_for_openhands_run(*, usage_id: str) -> str | None:
    session_enabled = _parse_bool(
        _first_env(
            "FEATURE_FACTORY_HUAWEI_LLM_SESSION_ENABLED",
            "LLM_SESSION_ENABLED",
        )
    )
    if session_enabled is False:
        return None
    explicit_session_id = _first_env(
        "FEATURE_FACTORY_HUAWEI_LLM_SESSION_ID",
        "LLM_SESSION_ID",
    )
    if explicit_session_id:
        return explicit_session_id
    safe_usage_id = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in usage_id
    )
    return f"{safe_usage_id}-{uuid.uuid4().hex}"


def _reasoning_enabled() -> bool:
    enabled = _parse_bool(_first_env(HUAWEI_LLM_ENABLE_REASONING_ENV))
    return True if enabled is None else enabled


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _env_int(names: tuple[str, ...], *, default: int) -> int:
    raw = _first_env(*names)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{names[0]} must be an integer") from exc


def _env_float(names: tuple[str, ...], *, default: float) -> float:
    raw = _first_env(*names)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{names[0]} must be a number") from exc


def _parse_bool(raw: str | None) -> bool | None:
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None