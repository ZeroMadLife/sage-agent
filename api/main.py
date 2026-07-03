"""FastAPI app factory with two-tier Agent."""

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from agents.graph import build_graph
from agents.itinerary_tool import create_itinerary_tool
from agents.react_agent import TourAgent
from core.config.settings import get_settings
from core.llm import create_llm
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

        # Phase 2 多Agent图（作为 generate_itinerary 工具的内部实现）
        graph = build_graph(
            weather_client=weather_client,
            scenic_client=scenic_client,
            planning_llm=planning_llm,
            budget_llm=budget_llm,
        )

        # 工具列表（generate_itinerary 是其中之一）
        tools: dict[str, Any] = {
            "search_nearby": amap_client.search_nearby,
            "get_poi_detail": amap_client.get_poi_detail,
            "search_attractions": amap_client.search_attractions,
            "get_weather": weather_client.get_weather,
            "get_forecast": weather_client.get_forecast,
            "get_route": amap_client.get_route,
            "geocode": amap_client.geocode,
            "search_scenic_spots": scenic_client.search_scenic_spots,
            "get_scenic_detail": scenic_client.get_scenic_detail,
            # 两段式核心：generate_itinerary 工具（内部走多Agent图）
            "generate_itinerary": create_itinerary_tool(graph=graph),
        }

        return TourAgent(llm=chat_llm, tools=tools)
    except Exception as exc:
        logger.warning("Runtime agent unavailable: %s", exc)
        return None


def create_app(agent: Any | None = None) -> FastAPI:
    """Create the TourSwarm API app.

    Args:
        agent: TourAgent 实例, None 时 API 可运行但 WebSocket 会报错
    """
    app = FastAPI(title="TourSwarm API")
    app.state.agent = agent
    from api import routes, ws
    app.include_router(routes.router)
    app.include_router(ws.router)
    return app


app = create_app(agent=create_runtime_agent())
