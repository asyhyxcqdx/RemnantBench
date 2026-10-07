from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from feature_factory.config import Settings
from feature_factory.llm_adapters.huawei_llm import (
    HUAWEI_LLM_ENABLE_REASONING_ENV,
    HUAWEI_LLM_NO_AUTH_API_KEY,
    HUAWEI_LLM_SESSION_HEADER,
    release_openhands_session,
    session_release_url,
)
from feature_factory.openhands_llm import (
    DEPLOYMENT_LLM_MODEL_ENV,
    HUAWEI_LLM_API_KEY_ENV,
    HUAWEI_LLM_BASE_URL_ENV,
    HUAWEI_LLM_MODEL_ENV,
    OPENHANDS_LLM_SSL_VERIFY_ENV,
    build_openhands_llm_config,
    openhands_llm_session_id,
    preserve_deployment_llm_model_env,
    set_openhands_llm_ssl_verify_env,
)


_LLM_ENV_NAMES = (
    "LLM_MODEL",
    "LLM_MODEL_NAME",
    "FEATURE_FACTORY_LLM_MODEL",
    "FEATURE_FACTORY_LLM_MODEL_NAME",
    HUAWEI_LLM_MODEL_ENV,
    "LLM_BASE_URL",
    "LLM_API_BASE_URL",
    "LLM_API_BASE",
    "OPENAI_BASE_URL",
    "FEATURE_FACTORY_LLM_BASE_URL",
    "FEATURE_FACTORY_LLM_API_BASE_URL",
    HUAWEI_LLM_BASE_URL_ENV,
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "FEATURE_FACTORY_LLM_API_KEY",
    HUAWEI_LLM_API_KEY_ENV,
    "LLM_CUSTOM_LLM_PROVIDER",
    "LLM_CUSTOM_PROVIDER",
    "FEATURE_FACTORY_HUAWEI_LLM_LITELLM_PROVIDER",
    "FEATURE_FACTORY_HUAWEI_LLM_SESSION_ID",
    "FEATURE_FACTORY_HUAWEI_LLM_SESSION_ENABLED",
    "FEATURE_FACTORY_HUAWEI_LLM_NUM_RETRIES",
    "FEATURE_FACTORY_HUAWEI_LLM_TEMPERATURE",
    "FEATURE_FACTORY_HUAWEI_LLM_TOP_P",
    "FEATURE_FACTORY_HUAWEI_LLM_MAX_OUTPUT_TOKENS",
    HUAWEI_LLM_ENABLE_REASONING_ENV,
    DEPLOYMENT_LLM_MODEL_ENV,
    "LLM_SESSION_ID",
    "LLM_SESSION_ENABLED",
    "LLM_EXTRA_HEADERS",
    "FEATURE_FACTORY_OPENHANDS_LLM_EXTRA_HEADERS",
    "LLM_NUM_RETRIES",
    "LLM_TIMEOUT",
    "LLM_TEMPERATURE",
    "LLM_TOP_P",
    "LLM_MAX_OUTPUT_TOKENS",
    "LLM_NATIVE_TOOL_CALLING",
    "FEATURE_FACTORY_OPENHANDS_LLM_TIMEOUT",
    "FEATURE_FACTORY_OPENHANDS_NATIVE_TOOL_CALLING",
    "FEATURE_FACTORY_LLM_NATIVE_TOOL_CALLING",
    "FEATURE_FACTORY_CONTAINER_HOST_GATEWAY",
    OPENHANDS_LLM_SSL_VERIFY_ENV,
    "SSL_VERIFY",
)


def _clear_llm_env(monkeypatch) -> None:
    for name in _LLM_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_set_openhands_llm_ssl_verify_env_serializes_supported_values(tmp_path) -> None:
    env: dict[str, str] = {}

    set_openhands_llm_ssl_verify_env(env, verify=False)
    assert env[OPENHANDS_LLM_SSL_VERIFY_ENV] == "false"

    set_openhands_llm_ssl_verify_env(env, verify=True)
    assert env[OPENHANDS_LLM_SSL_VERIFY_ENV] == "true"

    ca_bundle = tmp_path / "llm-ca.pem"
    set_openhands_llm_ssl_verify_env(env, verify=ca_bundle)
    assert env[OPENHANDS_LLM_SSL_VERIFY_ENV] == str(ca_bundle)


def test_openhands_llm_config_scopes_ssl_verify_to_serialized_llm(monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-5.4-mini-huawei")
    monkeypatch.setenv(OPENHANDS_LLM_SSL_VERIFY_ENV, "false")

    config = build_openhands_llm_config(usage_id="stage3-breaker")

    assert config.optional_kwargs["ssl_verify"] is False


def test_openhands_llm_config_accepts_legacy_bridge_ssl_verify(monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-5.4-mini-huawei")
    monkeypatch.setenv("SSL_VERIFY", "false")

    config = build_openhands_llm_config(usage_id="stage3-breaker")

    assert config.optional_kwargs["ssl_verify"] is False


def _install_fake_openhands_reasoning_modules(monkeypatch):
    class FakeMessage:
        _feature_factory_reasoning_field_aliases_enabled = False

        def __init__(
            self,
            *,
            role: str = "assistant",
            content: str = "",
            reasoning_content: str | None = None,
        ):
            self.role = role
            self.content = content
            self.reasoning_content = reasoning_content

        def to_chat_dict(self, *, send_reasoning_content: bool, **kwargs: Any) -> dict:
            message = {"role": self.role, "content": self.content}
            if send_reasoning_content and self.reasoning_content:
                message["reasoning_content"] = self.reasoning_content
            return message

        @classmethod
        def from_llm_chat_message(cls, message: Any) -> "FakeMessage":
            return cls(
                role=message.role,
                content=message.content,
                reasoning_content=getattr(message, "reasoning_content", None),
            )

        def model_copy(self, *, update: dict[str, Any]) -> "FakeMessage":
            copied = FakeMessage(
                role=self.role,
                content=self.content,
                reasoning_content=self.reasoning_content,
            )
            for key, value in update.items():
                setattr(copied, key, value)
            return copied

    class FakeNonNativeToolCallingMixin:
        _feature_factory_reasoning_field_aliases_enabled = False

        def post_response_prompt_mock(self, resp: Any, *_args: Any) -> Any:
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace())])

    message_module = types.ModuleType("openhands.sdk.llm.message")
    message_module.Message = FakeMessage

    model_features_module = types.ModuleType("openhands.sdk.llm.utils.model_features")
    model_features_module.SEND_REASONING_CONTENT_MODELS = []

    non_native_module = types.ModuleType("openhands.sdk.llm.mixins.non_native_fc")
    non_native_module.NonNativeToolCallingMixin = FakeNonNativeToolCallingMixin

    for name, module in {
        "openhands": types.ModuleType("openhands"),
        "openhands.sdk": types.ModuleType("openhands.sdk"),
        "openhands.sdk.llm": types.ModuleType("openhands.sdk.llm"),
        "openhands.sdk.llm.message": message_module,
        "openhands.sdk.llm.utils": types.ModuleType("openhands.sdk.llm.utils"),
        "openhands.sdk.llm.utils.model_features": model_features_module,
        "openhands.sdk.llm.mixins": types.ModuleType("openhands.sdk.llm.mixins"),
        "openhands.sdk.llm.mixins.non_native_fc": non_native_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    return FakeMessage, model_features_module


def test_openhands_llm_config_rewrites_localhost_base_url_for_container(
    monkeypatch,
) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "openai/kimi-k2.6-w4a8")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:8073/v1")
    monkeypatch.setenv("FEATURE_FACTORY_CONTAINER_HOST_GATEWAY", "172.18.0.1")

    config = build_openhands_llm_config(usage_id="stage2-planner")

    assert config.base_url == "http://172.18.0.1:8073/v1"


def test_openhands_llm_config_keeps_nonlocal_base_url(monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "openai/kimi-k2.6-w4a8")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("FEATURE_FACTORY_CONTAINER_HOST_GATEWAY", "172.18.0.1")

    config = build_openhands_llm_config(usage_id="stage2-planner")

    assert config.base_url == "https://llm.example.test/v1"


def test_huawei_llm_adapter_adapts_private_endpoint(monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv(DEPLOYMENT_LLM_MODEL_ENV, "glm-5-w4a8")
    monkeypatch.setenv("LLM_MODEL", "glm-5-w4a8")
    monkeypatch.setenv("LLM_BASE_URL", "http://10.43.2.226:7486/v1")
    monkeypatch.setenv("LLM_API_KEY", "dummy")
    monkeypatch.setenv("FEATURE_FACTORY_HUAWEI_LLM_SESSION_ID", "session-123")

    config = build_openhands_llm_config(usage_id="stage2-worker")

    assert config.model == "openai/glm-5-w4a8"
    assert config.api_key == "dummy"
    assert config.base_url == "http://10.43.2.226:7486/v1"
    assert config.optional_kwargs["timeout"] == 300
    assert config.optional_kwargs["num_retries"] == 10
    assert config.optional_kwargs["temperature"] == 0.7
    assert config.optional_kwargs["top_p"] == 0.95
    assert config.optional_kwargs["max_output_tokens"] == 16384
    assert config.optional_kwargs["native_tool_calling"] is True
    assert config.optional_kwargs["extra_headers"] == {
        HUAWEI_LLM_SESSION_HEADER: "session-123"
    }


def test_huawei_llm_adapter_allows_session_opt_out(monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv(DEPLOYMENT_LLM_MODEL_ENV, "glm-5-w4a8")
    monkeypatch.setenv("LLM_MODEL", "glm-5-w4a8")
    monkeypatch.setenv("LLM_BASE_URL", "http://10.43.2.226:7486/v1")
    monkeypatch.setenv("FEATURE_FACTORY_HUAWEI_LLM_SESSION_ENABLED", "0")

    config = build_openhands_llm_config(usage_id="stage3-breaker")

    assert config.model == "openai/glm-5-w4a8"
    assert config.api_key == HUAWEI_LLM_NO_AUTH_API_KEY
    assert "extra_headers" not in config.optional_kwargs


def test_huawei_llm_adapter_skips_when_ui_model_differs_from_deployment_model(
    monkeypatch,
) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv(DEPLOYMENT_LLM_MODEL_ENV, "glm-5-w4a8")
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    monkeypatch.setenv("LLM_BASE_URL", "http://10.43.2.226:7486/v1")

    config = build_openhands_llm_config(usage_id="stage2-worker")

    assert config.model == "custom-model"
    assert "extra_headers" not in config.optional_kwargs


def test_huawei_llm_adapter_matches_explicit_provider_prefixed_ui_model(
    monkeypatch,
) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv(DEPLOYMENT_LLM_MODEL_ENV, "glm-5-w4a8")
    monkeypatch.setenv("LLM_MODEL", "hosted_vllm/glm-5-w4a8")
    monkeypatch.setenv("LLM_CUSTOM_PROVIDER", "hosted_vllm")
    monkeypatch.setenv("LLM_BASE_URL", "http://10.43.2.226:7486/v1")

    config = build_openhands_llm_config(usage_id="stage2-worker")

    assert config.model == "hosted_vllm/glm-5-w4a8"
    assert config.optional_kwargs["native_tool_calling"] is True


def test_huawei_llm_adapter_supports_kimi_deployment_model(monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv(HUAWEI_LLM_MODEL_ENV, "kimi-k2.6-w4a8")
    monkeypatch.setenv("LLM_MODEL", "kimi-k2.6-w4a8")
    monkeypatch.setenv(HUAWEI_LLM_BASE_URL_ENV, "http://10.43.2.226:7486/v1")
    monkeypatch.setenv(HUAWEI_LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv("FEATURE_FACTORY_HUAWEI_LLM_SESSION_ENABLED", "0")

    config = build_openhands_llm_config(usage_id="stage4-issuer")

    assert config.model == "openai/kimi-k2.6-w4a8"
    assert config.api_key == "dummy"
    assert config.base_url == "http://10.43.2.226:7486/v1"
    assert config.optional_kwargs["native_tool_calling"] is True
    assert config.optional_kwargs["max_output_tokens"] == 16384
    assert "extra_headers" not in config.optional_kwargs


def test_huawei_llm_adapter_enables_reasoning_field_aliases(monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    message_cls, model_features = _install_fake_openhands_reasoning_modules(monkeypatch)
    monkeypatch.setenv(HUAWEI_LLM_MODEL_ENV, "kimi-k2.6-w4a8")
    monkeypatch.setenv("LLM_MODEL", "kimi-k2.6-w4a8")
    monkeypatch.setenv(HUAWEI_LLM_BASE_URL_ENV, "http://10.43.2.226:7486/v1")
    monkeypatch.setenv("FEATURE_FACTORY_HUAWEI_LLM_SESSION_ENABLED", "0")

    build_openhands_llm_config(usage_id="stage4-issuer")

    assert model_features.SEND_REASONING_CONTENT_MODELS == ["kimi-k2.6-w4a8"]

    outbound = message_cls(reasoning_content="think").to_chat_dict(
        cache_enabled=False,
        vision_enabled=False,
        function_calling_enabled=True,
        force_string_serializer=False,
        send_reasoning_content=True,
    )
    assert outbound["reasoning_content"] == "think"
    assert outbound["reasoning"] == "think"

    inbound = message_cls.from_llm_chat_message(
        SimpleNamespace(role="assistant", content="answer", reasoning="kimi think")
    )
    assert inbound.reasoning_content == "kimi think"


def test_huawei_llm_adapter_allows_reasoning_opt_out(monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    message_cls, model_features = _install_fake_openhands_reasoning_modules(monkeypatch)
    monkeypatch.setenv(HUAWEI_LLM_MODEL_ENV, "kimi-k2.6-w4a8")
    monkeypatch.setenv("LLM_MODEL", "kimi-k2.6-w4a8")
    monkeypatch.setenv(HUAWEI_LLM_BASE_URL_ENV, "http://10.43.2.226:7486/v1")
    monkeypatch.setenv("FEATURE_FACTORY_HUAWEI_LLM_SESSION_ENABLED", "0")
    monkeypatch.setenv(HUAWEI_LLM_ENABLE_REASONING_ENV, "0")

    build_openhands_llm_config(usage_id="stage4-issuer")

    assert model_features.SEND_REASONING_CONTENT_MODELS == []

    outbound = message_cls(reasoning_content="think").to_chat_dict(
        cache_enabled=False,
        vision_enabled=False,
        function_calling_enabled=True,
        force_string_serializer=False,
        send_reasoning_content=True,
    )
    assert outbound["reasoning_content"] == "think"
    assert "reasoning" not in outbound

    inbound = message_cls.from_llm_chat_message(
        SimpleNamespace(role="assistant", content="answer", reasoning="kimi think")
    )
    assert inbound.reasoning_content is None


def test_preserve_deployment_llm_model_env_captures_model_before_ui_override() -> None:
    env = {HUAWEI_LLM_MODEL_ENV: "glm-5-w4a8"}

    preserve_deployment_llm_model_env(env)
    env["LLM_MODEL"] = "custom-model"

    assert env[DEPLOYMENT_LLM_MODEL_ENV] == "glm-5-w4a8"


def test_huawei_llm_session_release_url_strips_v1() -> None:
    url = session_release_url(
        api_base="http://10.43.2.226:7486/v1",
        session_id="session with spaces",
    )

    assert (
        url == "http://10.43.2.226:7486/session_release?session_id=session+with+spaces"
    )


def test_huawei_llm_session_release_uses_llm_ssl_verify() -> None:
    response = MagicMock()
    response.__enter__.return_value = response
    llm = SimpleNamespace(
        base_url="https://172.17.0.1:5692/v1",
        extra_headers={HUAWEI_LLM_SESSION_HEADER: "session-abc"},
        ssl_verify=False,
    )

    with patch(
        "feature_factory.llm_adapters.huawei_llm.urllib.request.urlopen",
        return_value=response,
    ) as urlopen:
        release_openhands_session(llm=llm)

    ssl_context = urlopen.call_args.kwargs["context"]
    assert ssl_context.check_hostname is False
    assert ssl_context.verify_mode == 0


def test_openhands_llm_session_id_reads_adapter_header() -> None:
    llm = SimpleNamespace(extra_headers={HUAWEI_LLM_SESSION_HEADER: "session-abc"})

    assert openhands_llm_session_id(llm) == "session-abc"


def test_settings_accept_llm_model_name_and_api_base_aliases(
    monkeypatch, tmp_path
) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv(HUAWEI_LLM_MODEL_ENV, "glm-5-w4a8")
    monkeypatch.setenv(HUAWEI_LLM_BASE_URL_ENV, "http://10.43.2.226:7486/v1")
    monkeypatch.setenv(HUAWEI_LLM_API_KEY_ENV, "dummy")

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'settings.db'}")

    assert settings.stage2_llm_model == "glm-5-w4a8"
    assert settings.stage3_llm_model == "glm-5-w4a8"
    assert settings.stage4_llm_model == "glm-5-w4a8"
    assert settings.stage2_llm_base_url == "http://10.43.2.226:7486/v1"
    assert settings.stage3_llm_base_url == "http://10.43.2.226:7486/v1"
    assert settings.stage4_llm_base_url == "http://10.43.2.226:7486/v1"
    assert settings.stage2_llm_api_key is not None
    assert settings.stage2_llm_api_key.get_secret_value() == "dummy"