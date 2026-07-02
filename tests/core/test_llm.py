"""LLM factory tests."""

from unittest.mock import patch

import pytest

from core.llm import create_llm


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
        llm = create_llm("deepseek:deepseek-chat")

    assert llm is not None
    assert llm.model_name == "deepseek-chat"
    assert str(llm.openai_api_base) == "https://api.deepseek.com/v1"


def test_create_llm_uses_provider_default_model() -> None:
    """Empty model part falls back to provider default model."""
    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-ds"}, clear=True):
        llm = create_llm("deepseek:")

    assert llm.model_name == "deepseek-chat"


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
