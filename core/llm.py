"""LLM factory for OpenAI-compatible providers.

TourSwarm uses provider:model specs so agent code can request a model tier
without knowing each vendor's environment variable names.
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
        default_model="deepseek-chat",
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


def create_llm(model_spec: str, temperature: float = 0.0, **kwargs: Any) -> ChatOpenAI:
    """Create a ChatOpenAI instance from a provider:model spec.

    Args:
        model_spec: Provider-prefixed model spec, such as
            ``doubao:Doubao-Seed-2.0-pro`` or ``deepseek:deepseek-chat``.
        temperature: Sampling temperature. Defaults to deterministic output.
        **kwargs: Extra keyword arguments passed to ``ChatOpenAI``.

    Raises:
        ValueError: If the provider is unknown or the provider API key is absent.
    """
    provider, separator, model = model_spec.partition(":")
    provider = provider.strip()

    if not separator:
        raise ValueError("LLM model spec must use 'provider:model' format")

    config = _PROVIDERS.get(provider)
    if config is None:
        supported = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"未知 LLM provider: {provider}（支持: {supported}）")

    api_key = os.environ.get(config.api_key_env, "").strip()
    if not api_key:
        raise ValueError(
            f"LLM provider '{provider}' 的 API key 未配置" f"（环境变量: {config.api_key_env}）"
        )

    base_url = os.environ.get(config.base_url_env, config.default_base_url).strip()
    resolved_model = model.strip() or config.default_model

    return ChatOpenAI(
        api_key=SecretStr(api_key),
        base_url=base_url,
        model=resolved_model,
        temperature=temperature,
        **kwargs,
    )
