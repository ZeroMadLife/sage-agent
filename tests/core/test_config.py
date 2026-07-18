"""Configuration module tests."""

from core.config.settings import DEFAULT_QWEATHER_BASE_URL, DEFAULT_QWEATHER_GEO_URL, Settings


def test_settings_loads_from_env() -> None:
    """Settings load API keys from environment variables."""
    settings = Settings()
    assert settings.amap_api_key == "test-amap-key"
    assert settings.qweather_api_key == "test-weather-key"


def test_external_knowledge_parsing_is_fail_closed_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.knowledge_external_parsing_enabled is False
    assert settings.knowledge_external_allowed_source_ids == ""
    assert settings.knowledge_mineru_enabled is True
    assert settings.knowledge_qwen_vl_enabled is False


def test_sandbox_configuration_defaults_to_local_development() -> None:
    settings = Settings(_env_file=None)

    assert settings.sage_coding_sandbox_provider == "local_workspace"
    assert settings.sage_coding_sandbox_image == "python:3.11-slim"


def test_web_fetch_is_fail_closed_with_bounded_timeouts_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.sage_web_fetch_enabled is False
    assert settings.sage_web_fetch_connect_timeout_seconds == 5.0
    assert settings.sage_web_fetch_read_timeout_seconds == 10.0
    assert settings.sage_web_fetch_total_timeout_seconds == 20.0


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


def test_settings_has_access_codes(monkeypatch) -> None:
    """APP_ACCESS_CODES configures lightweight passphrase access."""
    monkeypatch.setenv("APP_ACCESS_CODES", "tour2026,friend01")

    settings = Settings()

    assert settings.app_access_codes == "tour2026,friend01"


def test_production_cloud_settings_fail_closed_without_secrets() -> None:
    """A production process must not silently start with placeholder credentials."""
    import pytest

    settings = Settings(app_env="production", app_secret_key="change-me-in-production")

    with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
        settings.validate_cloud_production_secrets()


def test_production_cloud_settings_accept_distinct_configured_secrets() -> None:
    settings = Settings(
        app_env="production",
        app_secret_key="application-secret-that-is-not-a-placeholder",
        github_oauth_client_id="client-id",
        github_oauth_client_secret="github-client-secret-that-is-long-enough-value",
        github_oauth_transaction_secret="transaction-secret-that-is-long-enough",
        github_token_encryption_secret="token-secret-that-is-long-enough-value",
        model_provider_encryption_secret="provider-secret-that-is-long-enough-value",
        cloud_frontend_url="https://sage.example",
        github_oauth_redirect_uri="https://sage.example/api/v1/cloud/auth/github/callback",
    )

    settings.validate_cloud_production_secrets()


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
    result = settings.resolve_llm("deepseek:deepseek-v4-flash")
    assert result["api_key"] == "test-ds-key"
    assert result["model"] == "deepseek-v4-flash"
    assert "deepseek.com" in result["base_url"]


def test_resolve_llm_defaults_to_main_model(monkeypatch) -> None:
    """resolve_llm 不传参时使用主模型。"""
    monkeypatch.setenv("LLM_MODEL", "doubao:Doubao-Seed-2.0-pro")
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


def test_resolve_llm_raises_on_missing_key(monkeypatch) -> None:
    """provider 的 key 未配置时抛出 ValueError。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    import pytest

    with pytest.raises(ValueError, match="API key 未配置"):
        settings.resolve_llm("openai:gpt-4o")
