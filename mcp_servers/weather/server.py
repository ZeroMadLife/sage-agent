"""Weather MCP Server exposing travel planning weather tools."""

import os
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from mcp_servers.weather.client import (
    DEFAULT_QWEATHER_BASE_URL,
    DEFAULT_QWEATHER_GEO_URL,
    CurrentWeather,
    DailyForecast,
    WeatherAlert,
    WeatherClient,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def create_weather_server(
    api_key: str,
    base_url: str = DEFAULT_QWEATHER_BASE_URL,
    geo_url: str = DEFAULT_QWEATHER_GEO_URL,
) -> FastMCP:
    """Create a Weather MCP Server instance."""
    server = FastMCP("weather-mcp-server")
    client = WeatherClient(api_key=api_key, base_url=base_url, geo_url=geo_url)

    @server.tool()
    async def get_weather(city: str) -> CurrentWeather:
        """查询指定城市的实时天气。

        在规划户外活动或行程前, 调用此工具检查目的地当前天气状况,
        判断是否适合出行。
        """
        city_info = await client.search_city(city)
        return await client.get_current_weather(location_id=city_info["location_id"])

    @server.tool()
    async def get_forecast(city: str, days: int = 7) -> list[DailyForecast]:
        """查询指定城市未来几天的天气预报。

        在规划多日行程时, 调用此工具获取每日天气, 用于安排室内/室外活动、
        选择出行日期和准备衣物。
        """
        city_info = await client.search_city(city)
        return await client.get_forecast(location_id=city_info["location_id"], days=days)

    @server.tool()
    async def get_weather_alert(city: str) -> list[WeatherAlert]:
        """查询指定城市的灾害预警信息。

        在出行前或行程中, 调用此工具检查是否有暴雨、高温、台风等预警,
        以便及时调整行程安排。
        """
        city_info = await client.search_city(city)
        return await client.get_weather_alert(location_id=city_info["location_id"])

    return server


def main() -> None:
    """Run the MCP server over stdio."""
    api_key = os.environ.get("QWEATHER_API_KEY", "")
    if not api_key:
        raise RuntimeError("QWEATHER_API_KEY 环境变量未设置")
    base_url = os.environ.get("QWEATHER_BASE_URL", DEFAULT_QWEATHER_BASE_URL)
    geo_url = os.environ.get("QWEATHER_GEO_URL", DEFAULT_QWEATHER_GEO_URL)
    server = create_weather_server(api_key=api_key, base_url=base_url, geo_url=geo_url)
    run: Callable[[], None] = server.run
    run()


if __name__ == "__main__":
    main()
