"""FastAPI app factory with two-tier Agent."""

import hashlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from agents.graph import build_graph
from agents.itinerary_tool import create_itinerary_tool
from agents.react_agent import AgentRuntime
from core.auth import AuthManager
from core.cloud.auth.repository import CloudRepository
from core.cloud.github import GitHubOAuthConfig, GitHubOAuthService
from core.cloud.model_providers import ModelProviderRepository, ProviderProbe
from core.coding.context import ModelCapabilityRegistry
from core.coding.provider_settings import SageProviderSettings, SageProviderSettingsStore
from core.coding.usage_store import UsageStore
from core.config.settings import get_settings
from core.llm import create_llm
from core.memory.compressor import ContextCompressor
from core.memory.session_store import SessionStore
from core.skill import SkillRegistry, build_travel_planning_skill
from db.database import AsyncSessionFactory
from mcp_servers.amap.client import AmapClient
from mcp_servers.scenic.client import ScenicClient
from mcp_servers.weather.client import WeatherClient

logger = logging.getLogger(__name__)
DEFAULT_PLANNING_MODEL = "doubao:Doubao-Seed-2.0-pro"
DEFAULT_CODING_MODEL = "deepseek:deepseek-v4-flash"
DEFAULT_CODING_MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "deepseek:deepseek-v4-flash",
        "label": "DeepSeek V4 Flash",
        "provider": "deepseek",
    },
    {
        "id": "deepseek:deepseek-v4-pro",
        "label": "DeepSeek V4 Pro",
        "provider": "deepseek",
    },
]


def _planning_model_spec(configured_model: str) -> str:
    """Resolve planning model specs with a demo-safe default."""
    return configured_model if ":" in configured_model else DEFAULT_PLANNING_MODEL


def create_runtime_agent() -> Any | None:
    """Build the two-tier Agent: ReAct main + generate_itinerary tool.

    构建两段式 Agent：
    - 主 Agent 用 DeepSeek（日常对话+意图理解）
    - generate_itinerary 工具内部用豆包+多Agent图（复杂规划）
    """
    try:
        settings = get_settings()
        repo_root = Path(__file__).resolve().parent.parent

        # 主 Agent 用 DeepSeek
        chat_llm: Any = create_llm("deepseek:deepseek-v4-flash")

        # 规划 Agent 用豆包
        model_spec = _planning_model_spec(settings.llm_model)
        planning_llm = create_llm(model_spec)
        budget_llm = create_llm(model_spec)

        # Clients
        weather_client = WeatherClient(
            api_key=settings.qweather_api_key,
            base_url=settings.qweather_base_url,
            geo_url=settings.qweather_geo_url,
        )
        scenic_client = ScenicClient(
            data_path=str(repo_root / "data" / "mock" / "scenic_spots.json")
        )
        amap_client = AmapClient(api_key=settings.amap_api_key)

        async def get_weather(city: str) -> Any:
            """Resolve a city name and fetch current weather for ReAct tools."""
            city_info = await weather_client.search_city(city)
            return await weather_client.get_current_weather(location_id=city_info["location_id"])

        async def get_forecast(city: str, days: int = 7) -> Any:
            """Resolve a city name and fetch forecast for ReAct tools."""
            city_info = await weather_client.search_city(city)
            return await weather_client.get_forecast(
                location_id=city_info["location_id"],
                days=days,
            )

        # Phase 2 多Agent图（作为 generate_itinerary 工具的内部实现）
        graph = build_graph(
            weather_client=weather_client,
            scenic_client=scenic_client,
            planning_llm=planning_llm,
            budget_llm=budget_llm,
        )

        generate_itinerary = create_itinerary_tool(graph=graph)

        # 工具列表（generate_itinerary 是其中之一）
        tools: dict[str, Any] = {
            "search_nearby": amap_client.search_nearby,
            "get_poi_detail": amap_client.get_poi_detail,
            "search_attractions": amap_client.search_attractions,
            "get_weather": get_weather,
            "get_forecast": get_forecast,
            "get_route": amap_client.get_route,
            "geocode": amap_client.geocode,
            "search_scenic_spots": scenic_client.search_scenic_spots,
            "get_scenic_detail": scenic_client.get_scenic_detail,
            # 两段式核心：generate_itinerary 工具（内部走多Agent图）
            "generate_itinerary": generate_itinerary,
        }

        registry = SkillRegistry()
        registry.register(build_travel_planning_skill(tools=tools, sub_agent_graph=graph))
        return AgentRuntime(llm=chat_llm, skill=registry.get("travel-planning"))
    except Exception as exc:
        logger.warning("Runtime agent unavailable: %s", exc)
        return None


def create_runtime_session_store() -> Any | None:
    """Build the runtime SessionStore without requiring external services at import time."""
    try:
        settings = get_settings()
        redis_module: Any = import_module("redis.asyncio")
        redis_client: Any = redis_module.from_url(settings.redis_url)
        compressor = None
        try:
            compressor = ContextCompressor(llm=create_llm(settings.llm_light_model))
        except Exception as exc:
            logger.warning("Context compressor unavailable: %s", exc)
        return SessionStore(redis_client, AsyncSessionFactory, compressor=compressor)
    except Exception as exc:
        logger.warning("Session store unavailable: %s", exc)
        return None


def create_app(
    agent: Any | None = None,
    auth: AuthManager | None = None,
    session_store: Any | None = None,
    coding_model_factory: Any | None = None,
    coding_workspace_root: str | Path | None = None,
    coding_storage_root: str | Path | None = None,
    coding_model_catalog: list[dict[str, Any]] | None = None,
    coding_model_capabilities: dict[str, object] | ModelCapabilityRegistry | None = None,
    coding_default_model: str | None = None,
    coding_checkpoint_anchor_key: bytes | None = None,
    cloud_repository: CloudRepository | None = None,
    cloud_dev_login_enabled: bool | None = None,
    cloud_secure_cookies: bool | None = None,
    cloud_app_env: str | None = None,
    cloud_github_oauth_service: GitHubOAuthService | None = None,
    cloud_frontend_url: str | None = None,
    cloud_model_provider_repository: ModelProviderRepository | None = None,
    cloud_model_provider_probe: ProviderProbe | None = None,
) -> FastAPI:
    """Create the Sage API app.

    Args:
        agent: travel-planning domain agent, None 时 API 可运行但 WebSocket 会报错
        auth: 口令验证器, None 时允许匿名访问
        session_store: 可选持久化会话存储
    """
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.coding_run_registry.shutdown()

    app = FastAPI(title="Sage API", lifespan=lifespan)
    app.state.agent = agent
    app.state.auth = auth
    app.state.session_store = session_store
    settings = get_settings()
    app_env = cloud_app_env or settings.app_env
    if app_env == "production" and cloud_repository is None:
        settings.validate_cloud_production_secrets()
    app.state.cloud_repository = cloud_repository or CloudRepository(AsyncSessionFactory)
    app.state.cloud_app_env = app_env
    app.state.cloud_dev_login_enabled = (
        settings.cloud_dev_login_enabled
        if cloud_dev_login_enabled is None
        else cloud_dev_login_enabled
    )
    app.state.cloud_secure_cookies = (
        True
        if app_env != "development"
        else settings.cloud_secure_cookies
        if cloud_secure_cookies is None
        else cloud_secure_cookies
    )
    app.state.cloud_frontend_url = cloud_frontend_url or settings.cloud_frontend_url
    provider_secret = settings.model_provider_encryption_secret
    if not provider_secret:
        provider_secret = hashlib.sha256(
            f"{settings.app_secret_key}:development-model-providers".encode()
        ).hexdigest()
    app.state.cloud_model_provider_repository = (
        cloud_model_provider_repository
        or ModelProviderRepository(
            AsyncSessionFactory,
            encryption_secret=provider_secret,
        )
    )
    app.state.cloud_model_provider_probe = cloud_model_provider_probe or ProviderProbe(
        app_env=app_env
    )
    app.state.cloud_github_oauth_service = cloud_github_oauth_service
    if app.state.cloud_github_oauth_service is None and all(
        (
            settings.github_oauth_client_id,
            settings.github_oauth_client_secret,
            settings.github_oauth_transaction_secret,
            settings.github_token_encryption_secret,
        )
    ):
        app.state.cloud_github_oauth_service = GitHubOAuthService(
            app.state.cloud_repository,
            GitHubOAuthConfig(
                client_id=settings.github_oauth_client_id,
                client_secret=settings.github_oauth_client_secret,
                redirect_uri=settings.github_oauth_redirect_uri,
                scope=settings.github_oauth_scope,
                transaction_secret=settings.github_oauth_transaction_secret,
                token_encryption_secret=settings.github_token_encryption_secret,
            ),
        )
    repo_root = Path(__file__).resolve().parent.parent
    resolved_workspace_root = Path(coding_workspace_root or repo_root).resolve()
    legacy_manifest_path = os.getenv(
        "SAGE_CODING_MODELS_FILE", str(repo_root / "config" / "coding_models.toml")
    )
    managed_settings_path = os.getenv("SAGE_CODING_SETTINGS_FILE") or None
    provider_settings_store = SageProviderSettingsStore(
        resolved_workspace_root,
        external_path=managed_settings_path,
        legacy_manifest_path=legacy_manifest_path,
    )
    resolved_settings: SageProviderSettings | None = None
    if coding_model_catalog is None and coding_model_capabilities is None:
        resolved_settings = provider_settings_store.load()
        resolved_catalog = resolved_settings.catalog
        resolved_capabilities = resolved_settings.registry
        resolved_default_model = coding_default_model or resolved_settings.default_model
    else:
        resolved_catalog = (
            DEFAULT_CODING_MODEL_CATALOG
            if coding_model_catalog is None
            else coding_model_catalog
        )
        resolved_capabilities = (
            coding_model_capabilities
            if isinstance(coding_model_capabilities, ModelCapabilityRegistry)
            else ModelCapabilityRegistry(coding_model_capabilities)
            if coding_model_capabilities is not None
            else ModelCapabilityRegistry.from_env()
        )
        resolved_default_model = coding_default_model or DEFAULT_CODING_MODEL
    app.state.coding_provider_settings_store = provider_settings_store
    app.state.coding_provider_settings = resolved_settings
    app.state.coding_provider_settings_available = resolved_settings is not None
    app.state.coding_model_catalog = resolved_catalog
    app.state.coding_default_model = resolved_default_model
    app.state.coding_model_capabilities = resolved_capabilities
    app.state.coding_model_reasoning_modes = (
        resolved_settings.reasoning_modes
        if resolved_settings is not None
        else {
            str(item["id"]): tuple(
                str(mode) for mode in item.get("reasoning_modes", [])
            )
            for item in resolved_catalog
        }
    )
    if coding_model_factory is None:
        def default_coding_model_factory(
            model_id: str = resolved_default_model,
            *,
            reasoning_mode: str = "off",
        ) -> Any:
            settings = app.state.coding_provider_settings
            if settings is None:
                return create_llm(model_id, reasoning_mode=reasoning_mode)
            return create_llm(
                model_id,
                provider_settings=settings,
                reasoning_mode=reasoning_mode,
            )

        app.state.coding_model_factory = default_coding_model_factory
    else:
        app.state.coding_model_factory = coding_model_factory
    app.state.coding_checkpoint_anchor_key = coding_checkpoint_anchor_key
    app.state.coding_workspace_root = resolved_workspace_root
    app.state.coding_storage_root = Path(coding_storage_root or (repo_root / ".coding")).resolve()
    app.state.coding_usage_store = UsageStore(
        resolved_workspace_root / ".sage" / "usage.sqlite3"
    )
    app.state.coding_sessions = {}
    from api.coding_runs import CodingRunRegistry

    app.state.coding_run_registry = CodingRunRegistry(app.state.coding_storage_root)

    from api import (
        assistant,
        cloud_auth,
        cloud_model_providers,
        cloud_workspaces,
        coding,
        routes,
        ws,
    )

    app.include_router(assistant.router)
    app.include_router(routes.router)
    app.include_router(ws.router)
    app.include_router(coding.router)
    app.include_router(cloud_auth.router)
    app.include_router(cloud_model_providers.router)
    app.include_router(cloud_workspaces.router)
    return app


app = create_app(
    agent=create_runtime_agent(),
    auth=AuthManager(get_settings().app_access_codes),
    session_store=create_runtime_session_store(),
)
