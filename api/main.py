"""FastAPI app factory with two-tier Agent."""

import logging
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from agents.graph import build_graph
from agents.itinerary_tool import create_itinerary_tool
from agents.react_agent import AgentRuntime
from core.auth import AuthManager
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
        chat_llm = create_llm("deepseek:deepseek-chat")

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
) -> FastAPI:
    """Create the TourSwarm API app.

    Args:
        agent: TourAgent 实例, None 时 API 可运行但 WebSocket 会报错
        auth: 口令验证器, None 时允许匿名访问
        session_store: 可选持久化会话存储
    """
    app = FastAPI(title="TourSwarm API")
    app.state.agent = agent
    app.state.auth = auth
    app.state.session_store = session_store
    from api import routes, ws

    app.include_router(routes.router)
    app.include_router(ws.router)
    return app


app = create_app(
    agent=create_runtime_agent(),
    auth=AuthManager(get_settings().app_access_codes),
    session_store=create_runtime_session_store(),
)
