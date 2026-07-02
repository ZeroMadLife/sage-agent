"""Configuration module tests."""

from core.config.settings import DEFAULT_QWEATHER_BASE_URL, DEFAULT_QWEATHER_GEO_URL, Settings


def test_settings_loads_from_env() -> None:
    """Settings load API keys from environment variables."""
    settings = Settings()
    assert settings.amap_api_key == "test-amap-key"
    assert settings.qweather_api_key == "test-weather-key"


def test_settings_has_amap_base_url() -> None:
    """Amap API base URL has a default value."""
    settings = Settings()
    assert "restapi.amap.com" in settings.amap_base_url


def test_settings_has_qweather_base_url(monkeypatch) -> None:
    """QWeather API base URL has a valid host."""
    monkeypatch.delenv("QWEATHER_BASE_URL", raising=False)
    monkeypatch.delenv("QWEATHER_GEO_URL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.qweather_base_url == DEFAULT_QWEATHER_BASE_URL
    assert settings.qweather_geo_url == DEFAULT_QWEATHER_GEO_URL


def test_resolve_llm_doubao(monkeypatch) -> None:
    """resolve_llm 正确解析 doubao provider。"""
    monkeypatch.setenv("DOUBAO_API_KEY", "test-doubao-key")
    settings = Settings()
    result = settings.resolve_llm("doubao:Doubao-Seed-2.0-pro")
    assert result["api_key"] == "test-doubao-key"
    assert result["model"] == "Doubao-Seed-2.0-pro"
    assert "ark.cn-beijing" in result["base_url"]


def test_resolve_llm_deepseek(monkeypatch) -> None:
    """resolve_llm 正确解析 deepseek provider。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-ds-key")
    settings = Settings()
    result = settings.resolve_llm("deepseek:deepseek-chat")
    assert result["api_key"] == "test-ds-key"
    assert result["model"] == "deepseek-chat"
    assert "deepseek.com" in result["base_url"]


def test_resolve_llm_defaults_to_main_model(monkeypatch) -> None:
    """resolve_llm 不传参时使用主模型。"""
    monkeypatch.setenv("DOUBAO_API_KEY", "test-doubao-key")
    settings = Settings()
    result = settings.resolve_llm()
    assert result["api_key"] == "test-doubao-key"
    assert result["model"] == "Doubao-Seed-2.0-pro"


def test_resolve_llm_raises_on_unknown_provider() -> None:
    """未知 provider 抛出 ValueError。"""
    settings = Settings()
    import pytest

    with pytest.raises(ValueError, match="未知 LLM provider"):
        settings.resolve_llm("unknown:model")


def test_resolve_llm_raises_on_missing_key() -> None:
    """provider 的 key 未配置时抛出 ValueError。"""
    settings = Settings()
    import pytest

    with pytest.raises(ValueError, match="API key 未配置"):
        settings.resolve_llm("openai:gpt-4o")
