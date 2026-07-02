"""TourSwarm global settings.

Configuration is loaded from environment variables and an optional ``.env`` file.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_QWEATHER_BASE_URL = "https://your-host.re.qweatherapi.com/v7"
DEFAULT_QWEATHER_GEO_URL = "https://your-host.re.qweatherapi.com/geoapi/v2"


class Settings(BaseSettings):
    """Application settings loaded via pydantic-settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    amap_api_key: str = Field(default="", description="Amap Web Service API key")
    amap_base_url: str = "https://restapi.amap.com/v3"

    qweather_api_key: str = Field(default="", description="QWeather API key")
    qweather_base_url: str = DEFAULT_QWEATHER_BASE_URL
    qweather_geo_url: str = DEFAULT_QWEATHER_GEO_URL

    caiyun_api_key: str = ""

    llm_provider: str = "doubao"

    # DeepSeek
    deepseek_api_key: str = Field(default="", description="DeepSeek API key")
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    # 火山引擎豆包（OpenAI 兼容格式，多模态）
    doubao_api_key: str = Field(default="", description="Volcengine Doubao API key")
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/coding/v3"
    doubao_model: str = "Doubao-Seed-2.0-pro"

    # 180txt 中转站（OpenAI 兼容格式）
    openai_proxy_api_key: str = Field(default="", description="180txt proxy API key")
    openai_proxy_base_url: str = "https://serve.wzjself.org/v1"
    openai_proxy_model: str = "gpt-5.4-mini"

    # OpenAI 官方
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    # 主模型 / 轻量模型，格式 provider:model
    llm_model: str = "doubao:Doubao-Seed-2.0-pro"
    llm_light_model: str = "deepseek:deepseek-chat"

    def resolve_llm(self, model_spec: str | None = None) -> dict[str, str]:
        """将 'provider:model' 解析为 {api_key, base_url, model}。

        Args:
            model_spec: 'provider:model' 格式，如 'doubao:Doubao-Seed-2.0-pro'。
                        为 None 时使用 llm_model（主模型）。

        Returns:
            {"api_key": str, "base_url": str, "model": str}
        """
        spec = model_spec or self.llm_model
        provider, _, model = spec.partition(":")
        provider = provider.strip()
        model = model.strip() or spec  # 如果没有冒号，整体当 model

        mapping: dict[str, tuple[str, str, str]] = {
            "deepseek": (
                self.deepseek_api_key,
                self.deepseek_base_url,
                model or self.deepseek_model,
            ),
            "doubao": (self.doubao_api_key, self.doubao_base_url, model or self.doubao_model),
            "openai_proxy": (
                self.openai_proxy_api_key,
                self.openai_proxy_base_url,
                model or self.openai_proxy_model,
            ),
            "openai": (self.openai_api_key, self.openai_base_url, model),
        }

        if provider not in mapping:
            raise ValueError(f"未知 LLM provider: {provider}（支持: {list(mapping)}）")

        api_key, base_url, resolved_model = mapping[provider]
        if not api_key:
            raise ValueError(f"LLM provider '{provider}' 的 API key 未配置")
        return {"api_key": api_key, "base_url": base_url, "model": resolved_model}

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "tourswarm"
    postgres_password: str = "tourswarm_dev"
    postgres_db: str = "tourswarm"

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    qdrant_host: str = "localhost"
    qdrant_port: int = 6333

    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_secret_key: str = "change-me-in-production"

    langsmith_api_key: str = ""
    langsmith_project: str = "tourswarm"

    mem0_vector_store: str = "qdrant"
    mem0_embedder_model: str = "BAAI/bge-large-zh-v1.5"

    @property
    def postgres_dsn(self) -> str:
        """Return the async PostgreSQL DSN."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """Return the Redis connection URL."""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    """Return cached global settings."""
    return Settings()
