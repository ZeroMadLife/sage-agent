"""Anthropic-native LLM client factory."""

import os
from typing import Any

from langchain_anthropic import ChatAnthropic
from pydantic import SecretStr


def create_anthropic_llm(model_spec: str, temperature: float = 0.0, **kwargs: Any) -> ChatAnthropic:
    provider, separator, model = model_spec.partition(":")
    provider = provider.strip()
    if not separator:
        raise ValueError("LLM model spec must use 'provider:model' format")
    if provider == "deepseek_anthropic":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "LLM provider 'deepseek_anthropic' 的 API key 未配置（环境变量: DEEPSEEK_API_KEY）"
            )
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic").strip()
        if not base_url.endswith("/anthropic"):
            base_url = base_url.rstrip("/") + "/anthropic"
        resolved_model = model.strip() or "deepseek-v4-flash"
        return ChatAnthropic(
            api_key=SecretStr(api_key),
            base_url=base_url,
            model_name=resolved_model,
            temperature=temperature,
            **kwargs,
        )
    elif provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "LLM provider 'anthropic' 的 API key 未配置（环境变量: ANTHROPIC_API_KEY）"
            )
        resolved_model = model.strip() or "claude-sonnet-4-20250514"
        return ChatAnthropic(
            api_key=SecretStr(api_key),
            model_name=resolved_model,
            temperature=temperature,
            **kwargs,
        )
    else:
        raise ValueError(
            f"非 Anthropic provider: {provider}（支持: anthropic, deepseek_anthropic）"
        )
