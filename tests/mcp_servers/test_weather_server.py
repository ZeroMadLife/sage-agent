"""Weather MCP Server tests."""

from typing import Any, cast
from unittest.mock import AsyncMock, patch

from mcp_servers.weather.server import create_weather_server


def _tools(server: Any) -> dict[str, Any]:
    return cast(dict[str, Any], server._tool_manager._tools)


def test_server_exposes_get_weather_tool() -> None:
    """Server exposes get_weather."""
    server = create_weather_server(api_key="test-key")
    assert "get_weather" in _tools(server)


def test_server_exposes_get_forecast_tool() -> None:
    """Server exposes get_forecast."""
    server = create_weather_server(api_key="test-key")
    assert "get_forecast" in _tools(server)


def test_server_exposes_get_weather_alert_tool() -> None:
    """Server exposes get_weather_alert."""
    server = create_weather_server(api_key="test-key")
    assert "get_weather_alert" in _tools(server)


def test_get_weather_tool_description_mentions_planning() -> None:
    """The weather tool description mentions weather context."""
    server = create_weather_server(api_key="test-key")
    tool = _tools(server)["get_weather"]
    assert "天气" in tool.description


def test_create_weather_server_passes_qweather_urls_to_client() -> None:
    """Server construction passes configured QWeather hosts to the client."""
    with patch("mcp_servers.weather.server.WeatherClient") as client_class:
        create_weather_server(
            api_key="test-key",
            base_url="https://weather.test/v7",
            geo_url="https://geo.test/geoapi/v2",
        )

    client_class.assert_called_once_with(
        api_key="test-key",
        base_url="https://weather.test/v7",
        geo_url="https://geo.test/geoapi/v2",
    )


async def test_get_weather_tool_delegates_to_client() -> None:
    """get_weather resolves city and delegates to current weather lookup."""
    weather = {"temp_c": 28, "text": "多云"}
    with patch("mcp_servers.weather.server.WeatherClient") as client_class:
        client = client_class.return_value
        client.search_city = AsyncMock(return_value={"location_id": "101210101"})
        client.get_current_weather = AsyncMock(return_value=weather)

        server = create_weather_server(api_key="test-key")
        result = await _tools(server)["get_weather"].run({"city": "杭州"})

    assert result == weather
    client.search_city.assert_awaited_once_with("杭州")
    client.get_current_weather.assert_awaited_once_with(location_id="101210101")


async def test_get_forecast_tool_delegates_to_client() -> None:
    """get_forecast resolves city and delegates to forecast lookup."""
    forecast = [{"date": "2026-07-01", "temp_max": 32}]
    with patch("mcp_servers.weather.server.WeatherClient") as client_class:
        client = client_class.return_value
        client.search_city = AsyncMock(return_value={"location_id": "101210101"})
        client.get_forecast = AsyncMock(return_value=forecast)

        server = create_weather_server(api_key="test-key")
        result = await _tools(server)["get_forecast"].run({"city": "杭州", "days": 7})

    assert result == forecast
    client.search_city.assert_awaited_once_with("杭州")
    client.get_forecast.assert_awaited_once_with(location_id="101210101", days=7)


async def test_get_weather_alert_tool_delegates_to_client() -> None:
    """get_weather_alert resolves city and delegates to alert lookup."""
    alerts = [{"title": "高温预警"}]
    with patch("mcp_servers.weather.server.WeatherClient") as client_class:
        client = client_class.return_value
        client.search_city = AsyncMock(return_value={"location_id": "101210101"})
        client.get_weather_alert = AsyncMock(return_value=alerts)

        server = create_weather_server(api_key="test-key")
        result = await _tools(server)["get_weather_alert"].run({"city": "杭州"})

    assert result == alerts
    client.search_city.assert_awaited_once_with("杭州")
    client.get_weather_alert.assert_awaited_once_with(location_id="101210101")
