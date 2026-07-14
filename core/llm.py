"""LLM factory - routes to OpenAI-compatible or Anthropic-native client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from core.llm_anthropic import create_anthropic_llm
from core.llm_openai import ProviderConfig, create_openai_llm
from core.llm_responses import ResponsesAPIChatModel

if TYPE_CHECKING:
    from core.coding.provider_settings import SageProviderSettings

_ANTHROPIC_PROVIDERS = {"anthropic", "deepseek_anthropic"}


def create_llm(
    model_spec: str,
    temperature: float = 0.0,
    *,
    provider_settings: SageProviderSettings | None = None,
    reasoning_mode: str = "off",
    api_key: str | None = None,
    base_url: str | None = None,
    api_mode: str | None = None,
    **kwargs: Any,
) -> ChatOpenAI | ChatAnthropic | ResponsesAPIChatModel:
    """Create an LLM client from a provider:model spec.

    Routes Anthropic-native providers (``anthropic``, ``deepseek_anthropic``)
    to :func:`core.llm_anthropic.create_anthropic_llm`; all other providers go
    to :func:`core.llm_openai.create_openai_llm`.

    Args:
        model_spec: Provider-prefixed model spec, such as
            ``doubao:Doubao-Seed-2.0-pro`` or ``deepseek:deepseek-v4-flash``.
        temperature: Sampling temperature. Defaults to deterministic output.
        **kwargs: Extra keyword arguments passed to the underlying client.

    Returns:
        A ``ChatOpenAI`` or ``ChatAnthropic`` instance. Both expose
        ``ainvoke`` and ``astream`` for async callers.
    """
    provider, separator, model = model_spec.partition(":")
    provider = provider.strip()
    if api_mode is not None:
        if not separator or not model.strip():
            raise ValueError("LLM model spec must use 'provider:model' format")
        if not api_key or not base_url:
            raise ValueError("direct Provider credentials require API key and Base URL")
        if api_mode == "anthropic_messages":
            return create_anthropic_llm(
                model_spec,
                temperature,
                api_key=api_key,
                base_url=base_url,
                **kwargs,
            )
        if api_mode == "openai_responses":
            reasoning_effort = None if reasoning_mode == "off" else reasoning_mode
            return ResponsesAPIChatModel(
                api_key=api_key,
                base_url=base_url,
                model=model.strip(),
                reasoning_effort=reasoning_effort,
            )
        if api_mode != "openai_chat_completions":
            raise ValueError("unsupported Provider API mode")
        return create_openai_llm(
            model_spec,
            temperature,
            provider_config=ProviderConfig("", "", base_url, model.strip()),
            api_key=api_key,
            base_url=base_url,
            **({**kwargs, **({"reasoning_effort": reasoning_mode} if reasoning_mode != "off" else {})}),
        )
    if provider_settings is not None:
        provider_definition = provider_settings.provider_for_model(model_spec)
        model_definition = provider_settings.model(model_spec)
        reasoning_kwargs = model_definition.reasoning.request_kwargs(reasoning_mode)
        resolved_kwargs = {**kwargs, **reasoning_kwargs}
        if provider_definition.api_mode == "anthropic_messages":
            return create_anthropic_llm(
                model_spec,
                temperature,
                api_key_env=provider_definition.api_key_env,
                base_url=provider_definition.base_url,
                **resolved_kwargs,
            )
        return create_openai_llm(
            model_spec,
            temperature,
            provider_config=ProviderConfig(
                api_key_env=provider_definition.api_key_env,
                base_url_env="",
                default_base_url=provider_definition.base_url,
                default_model=model_spec.partition(":")[2],
            ),
            base_url=provider_definition.base_url,
            **resolved_kwargs,
        )
    if provider in _ANTHROPIC_PROVIDERS:
        return create_anthropic_llm(model_spec, temperature, **kwargs)
    return create_openai_llm(model_spec, temperature, **kwargs)
