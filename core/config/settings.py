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

    amap_api_key: str = Field(default="", description="Amap Web Service API key", repr=False)
    amap_base_url: str = "https://restapi.amap.com/v3"

    qweather_api_key: str = Field(default="", description="QWeather API key", repr=False)
    qweather_base_url: str = DEFAULT_QWEATHER_BASE_URL
    qweather_geo_url: str = DEFAULT_QWEATHER_GEO_URL

    caiyun_api_key: str = Field(default="", repr=False)

    llm_provider: str = "doubao"

    # DeepSeek
    deepseek_api_key: str = Field(default="", description="DeepSeek API key", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-v4-flash"

    # 火山引擎豆包（OpenAI 兼容格式，多模态）
    doubao_api_key: str = Field(default="", description="Volcengine Doubao API key", repr=False)
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/coding/v3"
    doubao_model: str = "Doubao-Seed-2.0-pro"

    # 180txt 中转站（OpenAI 兼容格式）
    openai_proxy_api_key: str = Field(default="", description="180txt proxy API key", repr=False)
    openai_proxy_base_url: str = "https://serve.wzjself.org/v1"
    openai_proxy_model: str = "gpt-5.4-mini"

    # OpenAI 官方
    openai_api_key: str = Field(default="", repr=False)
    openai_base_url: str = "https://api.openai.com/v1"

    # 主模型 / 轻量模型，格式 provider:model
    llm_model: str = "doubao:Doubao-Seed-2.0-pro"
    llm_light_model: str = "deepseek:deepseek-v4-flash"

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

    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_secret_key: str = "change-me-in-production"
    app_access_codes: str = ""

    cloud_dev_login_enabled: bool = False
    # Local HTTP development needs a non-Secure cookie; non-development is
    # forced to Secure by the app factory regardless of this value.
    cloud_secure_cookies: bool = False
    cloud_frontend_url: str = "http://localhost:5173"
    sage_deerflow_v2_enabled: bool = True
    sage_coding_default_runtime_profile: str = "deerflow_v2"
    sage_mcp_live_enabled: bool = False
    sage_coding_sandbox_provider: str = "local_workspace"
    sage_coding_sandbox_image: str = "python:3.11-slim"

    # GitHub OAuth is used for identity only. Repository authorization is
    # handled separately by a GitHub App so private repository access can be
    # granted per repository with short-lived installation tokens.
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    github_oauth_redirect_uri: str = "http://localhost:8000/api/v1/cloud/auth/github/callback"
    github_oauth_scope: str = "read:user user:email"
    github_oauth_transaction_secret: str = ""
    github_token_encryption_secret: str = ""
    model_provider_encryption_secret: str = ""

    # Local V7.2 Knowledge Workspace. Cloud mode remains disabled until the
    # repository and metadata store are tenant-scoped.
    knowledge_workspace_root: str = ""
    knowledge_database_path: str = ""
    knowledge_source_root: str = ""
    knowledge_source_id: str = "sage-learning"
    knowledge_source_label: str = "Sage Learning"
    knowledge_source_kind: str = "obsidian"
    knowledge_jobs_enabled: bool = False
    # External parsing is a separate trust boundary. It stays disabled until
    # both a source-root allowlist and at least one adapter are configured.
    knowledge_external_parsing_enabled: bool = False
    knowledge_external_allowed_source_ids: str = ""
    knowledge_external_timeout_seconds: float = Field(default=180.0, ge=10.0, le=900.0)
    knowledge_mineru_enabled: bool = True
    knowledge_mineru_base_url: str = "https://mineru.net/api/v1/agent"
    knowledge_mineru_poll_seconds: float = Field(default=1.0, gt=0.0, le=30.0)
    knowledge_qwen_vl_enabled: bool = False
    knowledge_qwen_vl_api_key: str = Field(default="", repr=False)
    knowledge_qwen_vl_base_url: str = (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    knowledge_qwen_vl_model: str = "qwen3-vl-flash"
    knowledge_qwen_vl_max_pages: int = Field(default=12, ge=1, le=20)

    langsmith_api_key: str = ""
    langsmith_project: str = "tourswarm"

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

    def validate_cloud_production_secrets(self) -> None:
        """Fail closed when a production cloud process has placeholder secrets."""
        if self.app_env != "production":
            return
        missing: list[str] = []
        if (
            not self.app_secret_key
            or self.app_secret_key == "change-me-in-production"
            or len(self.app_secret_key) < 32
        ):
            missing.append("APP_SECRET_KEY")
        for name, value in (
            ("GITHUB_OAUTH_CLIENT_ID", self.github_oauth_client_id),
            ("GITHUB_OAUTH_CLIENT_SECRET", self.github_oauth_client_secret),
            ("GITHUB_OAUTH_TRANSACTION_SECRET", self.github_oauth_transaction_secret),
            ("GITHUB_TOKEN_ENCRYPTION_SECRET", self.github_token_encryption_secret),
            ("MODEL_PROVIDER_ENCRYPTION_SECRET", self.model_provider_encryption_secret),
        ):
            if not value or (len(value) < 32 and "CLIENT_ID" not in name):
                missing.append(name)
        if not self.cloud_frontend_url.startswith("https://"):
            missing.append("CLOUD_FRONTEND_URL(HTTPS)")
        if not self.github_oauth_redirect_uri.startswith("https://"):
            missing.append("GITHUB_OAUTH_REDIRECT_URI(HTTPS)")
        if self.cloud_dev_login_enabled:
            missing.append("CLOUD_DEV_LOGIN_ENABLED=false")
        if missing:
            raise RuntimeError(f"production cloud secrets are missing: {', '.join(missing)}")


@lru_cache
def get_settings() -> Settings:
    """Return cached global settings."""
    return Settings()
