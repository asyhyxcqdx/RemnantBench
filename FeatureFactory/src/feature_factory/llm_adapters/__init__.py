from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from feature_factory.llm_adapters import huawei_llm

if TYPE_CHECKING:
    from feature_factory.openhands_llm import OpenHandsLLMConfig


def apply_openhands_llm_adapters(
    config: "OpenHandsLLMConfig",
    *,
    usage_id: str,
) -> "OpenHandsLLMConfig":
    return huawei_llm.adapt_openhands_llm_config(config, usage_id=usage_id)


def openhands_llm_adapter_session_id(llm: Any) -> str | None:
    return huawei_llm.openhands_session_id(llm)


def release_openhands_llm_adapter_resources(
    *,
    llm: Any,
    on_error: Callable[[str], None] | None = None,
) -> None:
    huawei_llm.release_openhands_session(llm=llm, on_error=on_error)
