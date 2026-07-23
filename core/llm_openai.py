"""OpenAI-compatible LLM client factory.

TourSwarm uses provider:model specs so agent code can request a model tier
without knowing each vendor's environment variable names.

This module only handles OpenAI-compatible providers. Anthropic-native providers
(``anthropic``, ``deepseek_anthropic``) are handled by :mod:`core.llm_anthropic`.
"""

import os
from typing import Any, NamedTuple

from langchain_openai import ChatOpenAI
from pydantic import SecretStr


class ProviderConfig(NamedTuple):
    """Environment mapping for one OpenAI-compatible provider."""

    api_key_env: str
    base_url_env: str
    default_base_url: str
    default_model: str


_PROVIDERS: dict[str, ProviderConfig] = {
    "doubao": ProviderConfig(
        api_key_env="DOUBAO_API_KEY",
        base_url_env="DOUBAO_BASE_URL",
        default_base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        default_model="Doubao-Seed-2.0-pro",
    ),
    "deepseek": ProviderConfig(
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
        default_base_url="https://api.deepseek.com/v1",
        default_model="deepseek-v4-flash",
    ),
    "deepseek_anthropic": ProviderConfig(
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
        default_base_url="https://api.deepseek.com/anthropic",
        default_model="deepseek-v4-flash",
    ),
    "openai_proxy": ProviderConfig(
        api_key_env="OPENAI_PROXY_API_KEY",
        base_url_env="OPENAI_PROXY_BASE_URL",
        default_base_url="https://serve.wzjself.org/v1",
        default_model="gpt-5.4-mini",
    ),
    "openai": ProviderConfig(
        api_key_env="OPENAI_API_KEY",
        base_url_env="OPENAI_BASE_URL",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
    ),
}

# Anthropic-native providers must not be routed through the OpenAI client.
_ANTHROPIC_PROVIDERS = {"anthropic", "deepseek_anthropic"}


def create_openai_llm(
    model_spec: str,
    temperature: float = 0.0,
    *,
    provider_config: ProviderConfig | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    http_async_client: Any | None = None,
    **kwargs: Any,
) -> ChatOpenAI:
    """Create a ChatOpenAI instance from a provider:model spec.

    Args:
        model_spec: Provider-prefixed model spec, such as
            ``doubao:Doubao-Seed-2.0-pro`` or ``deepseek:deepseek-v4-flash``.
        temperature: Sampling temperature. Defaults to deterministic output.
        **kwargs: Extra keyword arguments passed to ``ChatOpenAI``.

    Raises:
        ValueError: If the provider is unknown, is Anthropic-native, or the
            provider API key is absent.
    """
    provider, separator, model = model_spec.partition(":")
    provider = provider.strip()

    if not separator:
        raise ValueError("LLM model spec must use 'provider:model' format")

    if provider in _ANTHROPIC_PROVIDERS:
        raise ValueError(
            f"LLM provider '{provider}' 走 Anthropic 原生路径，"
            f"请使用 create_anthropic_llm / create_llm 路由"
        )

    config = provider_config or _PROVIDERS.get(provider)
    if config is None:
        supported = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"未知 LLM provider: {provider}（支持: {supported}）")

    resolved_api_key = (
        api_key.strip() if api_key is not None else os.environ.get(config.api_key_env, "").strip()
    )
    if not resolved_api_key:
        raise ValueError(
            f"LLM provider '{provider}' 的 API key 未配置" f"（环境变量: {config.api_key_env}）"
        )

    resolved_base_url = (
        base_url.strip()
        if base_url is not None
        else (
            os.environ.get(config.base_url_env, config.default_base_url)
            if config.base_url_env
            else config.default_base_url
        ).strip()
    )
    resolved_model = model.strip() or config.default_model
    kwargs.setdefault("stream_usage", True)

    return ChatOpenAI(
        api_key=SecretStr(resolved_api_key),
        base_url=resolved_base_url,
        model=resolved_model,
        temperature=temperature,
        http_async_client=http_async_client,
        **kwargs,
    )
