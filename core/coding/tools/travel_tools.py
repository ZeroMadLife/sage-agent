"""Deferred travel-domain tools for Sage."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, Field, field_validator

from agents.graph import build_graph
from agents.itinerary_tool import create_itinerary_tool
from core.coding.context import WorkspaceContext
from core.coding.tools.base import ToolContext, ToolResult
from core.coding.tools.registry import register_tool
from core.config.settings import Settings, get_settings
from core.llm import create_llm
from mcp_servers.amap.client import AmapClient, RouteMode
from mcp_servers.scenic.client import ScenicClient
from mcp_servers.weather.client import WeatherClient

T = TypeVar("T")


class GenerateItineraryArgs(BaseModel):
    """Arguments for generate_itinerary."""

    destination: str
    budget_total: int = 500
    preferences: str | list[str] = ""
    dates: str | dict[str, str] = ""
    duration_days: int | str | None = None

    @field_validator("destination")
    @classmethod
    def destination_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("destination must not be empty")
        return value


class SearchAttractionsArgs(BaseModel):
    """Arguments for search_attractions."""

    city: str
    keywords: str = ""
    category: str = ""
    limit: int = Field(default=20, ge=1, le=50)


class WeatherArgs(BaseModel):
    """Arguments for get_weather."""

    city: str


class ForecastArgs(BaseModel):
    """Arguments for get_forecast."""

    city: str
    days: int = Field(default=7, ge=1, le=7)


class GeocodeArgs(BaseModel):
    """Arguments for geocode."""

    address: str
    city: str = ""


class SearchNearbyArgs(BaseModel):
    """Arguments for search_nearby."""

    location: str
    radius: int = Field(default=1000, ge=1, le=50000)
    keywords: str = ""
    types: str = ""
    limit: int = Field(default=20, ge=1, le=50)


class RouteArgs(BaseModel):
    """Arguments for get_route."""

    origin: str
    destination: str
    mode: RouteMode = "walking"


@register_tool(
    name="generate_itinerary",
    description="生成完整多日旅游行程（内部多Agent协作：信息、推荐、规划、预算）。",
    schema={
        "destination": "str",
        "budget_total": "int",
        "preferences": "str | list[str]",
        "dates": "str | {start: str, end: str}",
        "duration_days": "int | str",
    },
    schema_model=GenerateItineraryArgs,
    risky=False,
    category="travel",
    timeout=90.0,
    deferred=True,
)
def generate_itinerary(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Call the existing LangGraph travel planner as a Sage tool."""
    _ = tool_context
    try:
        settings = get_settings()
        graph = _build_itinerary_graph(settings, workspace.root)
        itinerary_tool = create_itinerary_tool(graph)
        result = _run_async(
            itinerary_tool(
                destination=str(args["destination"]),
                budget_total=args.get("budget_total", 500),
                preferences=args.get("preferences", ""),
                dates=args.get("dates", ""),
                duration_days=args.get("duration_days"),
            )
        )
        return _json_result(result)
    except Exception as exc:
        return ToolResult(content=f"travel tool error: {exc}", is_error=True)


@register_tool(
    name="search_attractions",
    description="搜索指定城市的景点 POI。",
    schema={"city": "str", "keywords": "str", "category": "str", "limit": "int"},
    schema_model=SearchAttractionsArgs,
    risky=False,
    category="travel",
    deferred=True,
)
def search_attractions(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Search Amap attractions."""
    _ = workspace, tool_context
    return _with_amap(
        lambda client: client.search_attractions(
            city=str(args["city"]),
            keywords=str(args.get("keywords", "")),
            category=str(args.get("category", "")),
            limit=int(args.get("limit", 20)),
        )
    )


@register_tool(
    name="get_weather",
    description="查询城市当前天气。",
    schema={"city": "str"},
    schema_model=WeatherArgs,
    risky=False,
    category="travel",
    deferred=True,
)
def get_weather(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Fetch current QWeather data for a city."""
    _ = workspace, tool_context
    return _with_weather(
        lambda client: _weather_for_city(client, str(args["city"])),
    )


@register_tool(
    name="get_forecast",
    description="查询城市未来天气预报。",
    schema={"city": "str", "days": "int"},
    schema_model=ForecastArgs,
    risky=False,
    category="travel",
    deferred=True,
)
def get_forecast(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Fetch QWeather forecast data for a city."""
    _ = workspace, tool_context
    return _with_weather(
        lambda client: _forecast_for_city(client, str(args["city"]), int(args.get("days", 7))),
    )


@register_tool(
    name="geocode",
    description="把地址转换为高德经纬度坐标。",
    schema={"address": "str", "city": "str"},
    schema_model=GeocodeArgs,
    risky=False,
    category="travel",
    deferred=True,
)
def geocode(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Geocode an address with Amap."""
    _ = workspace, tool_context
    return _with_amap(
        lambda client: client.geocode(
            address=str(args["address"]),
            city=str(args.get("city", "")),
        )
    )


@register_tool(
    name="search_nearby",
    description="搜索指定经纬度附近的 POI。",
    schema={
        "location": "str",
        "radius": "int",
        "keywords": "str",
        "types": "str",
        "limit": "int",
    },
    schema_model=SearchNearbyArgs,
    risky=False,
    category="travel",
    deferred=True,
)
def search_nearby(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Search nearby Amap POIs."""
    _ = workspace, tool_context
    return _with_amap(
        lambda client: client.search_nearby(
            location=str(args["location"]),
            radius=int(args.get("radius", 1000)),
            keywords=str(args.get("keywords", "")),
            types=str(args.get("types", "")),
            limit=int(args.get("limit", 20)),
        )
    )


@register_tool(
    name="get_route",
    description="规划两点间路线，返回距离、耗时和步骤。",
    schema={"origin": "str", "destination": "str", "mode": "walking | driving | transit"},
    schema_model=RouteArgs,
    risky=False,
    category="travel",
    deferred=True,
)
def get_route(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Plan a route with Amap."""
    _ = workspace, tool_context
    return _with_amap(
        lambda client: client.get_route(
            origin=str(args["origin"]),
            destination=str(args["destination"]),
            mode=args.get("mode", "walking"),
        )
    )


def _build_itinerary_graph(settings: Settings, repo_root: Path) -> Any:
    if not settings.qweather_api_key:
        raise ValueError("QWEATHER_API_KEY 未配置，无法调用 generate_itinerary")
    weather_client = WeatherClient(
        api_key=settings.qweather_api_key,
        base_url=settings.qweather_base_url,
        geo_url=settings.qweather_geo_url,
    )
    scenic_client = ScenicClient(data_path=str(repo_root / "data" / "mock" / "scenic_spots.json"))
    planning_llm = create_llm(settings.llm_model)
    budget_llm = create_llm(settings.llm_model)
    return build_graph(
        weather_client=weather_client,
        scenic_client=scenic_client,
        planning_llm=planning_llm,
        budget_llm=budget_llm,
    )


def _with_amap(call: Callable[[AmapClient], Awaitable[T]]) -> ToolResult:
    settings = get_settings()
    if not settings.amap_api_key:
        return ToolResult(content="AMAP_API_KEY 未配置，无法调用旅游高德工具", is_error=True)

    async def invoke() -> T:
        client = AmapClient(api_key=settings.amap_api_key, base_url=settings.amap_base_url)
        try:
            return await call(client)
        finally:
            await client.close()

    try:
        return _json_result(_run_async(invoke()))
    except Exception as exc:
        return ToolResult(content=f"travel tool error: {exc}", is_error=True)


def _with_weather(call: Callable[[WeatherClient], Awaitable[T]]) -> ToolResult:
    settings = get_settings()
    if not settings.qweather_api_key:
        return ToolResult(content="QWEATHER_API_KEY 未配置，无法调用旅游天气工具", is_error=True)

    async def invoke() -> T:
        client = WeatherClient(
            api_key=settings.qweather_api_key,
            base_url=settings.qweather_base_url,
            geo_url=settings.qweather_geo_url,
        )
        try:
            return await call(client)
        finally:
            await client.close()

    try:
        return _json_result(_run_async(invoke()))
    except Exception as exc:
        return ToolResult(content=f"travel tool error: {exc}", is_error=True)


async def _weather_for_city(client: WeatherClient, city: str) -> dict[str, Any]:
    city_info = await client.search_city(city)
    weather = await client.get_current_weather(city_info["location_id"])
    return {"city": city_info, "weather": weather}


async def _forecast_for_city(client: WeatherClient, city: str, days: int) -> dict[str, Any]:
    city_info = await client.search_city(city)
    forecast = await client.get_forecast(city_info["location_id"], days=days)
    return {"city": city_info, "forecast": forecast}


def _run_async(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _json_result(value: Any) -> ToolResult:
    return ToolResult(content=json.dumps(value, ensure_ascii=False, indent=2))
