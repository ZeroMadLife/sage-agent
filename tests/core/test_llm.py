"""LLM factory tests."""

from unittest.mock import patch

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from core.coding.provider_settings import SageProviderSettings
from core.llm import create_llm


def _provider_settings(*, anthropic: bool = False) -> SageProviderSettings:
    provider = "anthropic" if anthropic else "openai"
    api_mode = "anthropic_messages" if anthropic else "openai_chat_completions"
    reasoning = (
        {
            "kind": "anthropic_thinking_budget",
            "budgets": {"low": 1024, "medium": 4096, "high": 8192},
        }
        if anthropic
        else {
            "kind": "openai_reasoning_effort",
            "modes": ["low", "medium", "high"],
        }
    )
    return SageProviderSettings.from_mapping(
        {
            "version": 1,
            "default_model": f"{provider}:test-model",
            "providers": [
                {
                    "id": provider,
                    "label": provider.title(),
                    "api_mode": api_mode,
                    "base_url": f"https://api.{provider}.com/v1",
                    "api_key_env": f"{provider.upper()}_API_KEY",
                    "models": [
                        {
                            "id": f"{provider}:test-model",
                            "label": "Test Model",
                            "reasoning": reasoning,
                        }
                    ],
                }
            ],
        }
    )


def test_create_llm_doubao() -> None:
    """create_llm creates Doubao ChatOpenAI-compatible models."""
    with patch.dict(
        "os.environ",
        {
            "DOUBAO_API_KEY": "test-doubao",
            "DOUBAO_BASE_URL": "https://ark.cn-beijing.volces.com/api/coding/v3",
        },
        clear=True,
    ):
        llm = create_llm("doubao:Doubao-Seed-2.0-pro")

    assert llm is not None
    assert llm.model_name == "Doubao-Seed-2.0-pro"
    assert str(llm.openai_api_base) == "https://ark.cn-beijing.volces.com/api/coding/v3"


def test_create_llm_deepseek() -> None:
    """create_llm creates DeepSeek ChatOpenAI-compatible models."""
    with patch.dict(
        "os.environ",
        {
            "DEEPSEEK_API_KEY": "test-ds",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
        },
        clear=True,
    ):
        llm = create_llm("deepseek:deepseek-v4-flash")

    assert llm is not None
    assert llm.model_name == "deepseek-v4-flash"
    assert str(llm.openai_api_base) == "https://api.deepseek.com/v1"


def test_create_llm_uses_provider_default_model() -> None:
    """Empty model part falls back to provider default model."""
    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-ds"}, clear=True):
        llm = create_llm("deepseek:")

    assert llm.model_name == "deepseek-v4-flash"


def test_create_llm_unknown_provider_raises() -> None:
    """Unknown providers raise a clear ValueError."""
    with pytest.raises(ValueError, match="未知 LLM provider"):
        create_llm("unknown:model")


def test_create_llm_missing_key_raises() -> None:
    """Missing provider API keys raise a clear ValueError."""
    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(ValueError, match="API key"),
    ):
        create_llm("openai:gpt-4o")


def test_create_llm_routes_to_openai() -> None:
    """OpenAI-compatible providers return a ChatOpenAI instance."""
    with patch.dict(
        "os.environ",
        {
            "DEEPSEEK_API_KEY": "test-ds",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
        },
        clear=True,
    ):
        llm = create_llm("deepseek:deepseek-v4-flash")

    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "deepseek-v4-flash"


def test_create_llm_routes_to_anthropic() -> None:
    """Anthropic-native providers (deepseek_anthropic) return a ChatAnthropic instance."""
    with patch.dict(
        "os.environ",
        {
            "DEEPSEEK_API_KEY": "test-ds",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
        },
        clear=True,
    ):
        llm = create_llm("deepseek_anthropic:deepseek-v4-flash")

    assert isinstance(llm, ChatAnthropic)
    assert llm.model == "deepseek-v4-flash"
    # Non-anthropic base URL is suffixed with /anthropic.
    assert str(llm.anthropic_api_url) == "https://api.deepseek.com/v1/anthropic"


def test_create_llm_deepseek_anthropic_missing_key_raises() -> None:
    """deepseek_anthropic without a key raises a clear ValueError."""
    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(ValueError, match="DEEPSEEK_API_KEY"),
    ):
        create_llm("deepseek_anthropic:deepseek-v4-flash")


def test_create_llm_applies_configured_openai_reasoning() -> None:
    settings = _provider_settings()
    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai"}, clear=True):
        llm = create_llm(
            "openai:test-model",
            provider_settings=settings,
            reasoning_mode="medium",
        )

    assert isinstance(llm, ChatOpenAI)
    assert llm.reasoning_effort == "medium"
    assert llm.stream_usage is True


def test_create_llm_applies_configured_anthropic_thinking() -> None:
    settings = _provider_settings(anthropic=True)
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-anthropic"}, clear=True):
        llm = create_llm(
            "anthropic:test-model",
            provider_settings=settings,
            reasoning_mode="high",
        )

    assert isinstance(llm, ChatAnthropic)
    assert llm.thinking == {"type": "enabled", "budget_tokens": 8192}


def test_configured_llm_rejects_reasoning_not_declared_by_model() -> None:
    settings = _provider_settings()
    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai"}, clear=True),
        pytest.raises(ValueError, match="unsupported reasoning mode"),
    ):
        create_llm(
            "openai:test-model",
            provider_settings=settings,
            reasoning_mode="max",
        )
