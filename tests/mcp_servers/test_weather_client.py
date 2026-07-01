"""QWeather API client tests."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from mcp_servers.weather.client import WeatherClient

MOCK_DIR = Path(__file__).parent.parent.parent / "data" / "mock"


@pytest.fixture
def mock_now_response() -> dict[str, Any]:
    """Return a mocked current weather response."""
    return json.loads((MOCK_DIR / "weather_now.json").read_text(encoding="utf-8"))


@pytest.fixture
def mock_forecast_response() -> dict[str, Any]:
    """Return a mocked weather forecast response."""
    return json.loads((MOCK_DIR / "weather_forecast.json").read_text(encoding="utf-8"))


@pytest.fixture
def client() -> WeatherClient:
    """Return a QWeather test client."""
    return WeatherClient(api_key="test-weather-key")


@respx.mock
async def test_get_current_weather_returns_normalized(
    client: WeatherClient, mock_now_response: dict[str, Any]
) -> None:
    """Current weather returns a normalized structure."""
    respx.get("https://api.qweather.com/v7/weather/now").mock(
        return_value=httpx.Response(200, json=mock_now_response)
    )
    result = await client.get_current_weather(location_id="101210101")

    assert result["temp_c"] == 28
    assert result["text"] == "多云"
    assert result["humidity"] == 65
    assert result["wind_dir"] == "南风"


@respx.mock
async def test_get_forecast_returns_daily_list(
    client: WeatherClient, mock_forecast_response: dict[str, Any]
) -> None:
    """Forecast returns a daily weather list."""
    respx.get("https://api.qweather.com/v7/weather/7d").mock(
        return_value=httpx.Response(200, json=mock_forecast_response)
    )
    result = await client.get_forecast(location_id="101210101", days=7)

    assert len(result) == 2
    assert result[0]["date"] == "2026-07-01"
    assert result[0]["temp_max"] == 32
    assert result[0]["temp_min"] == 24


@respx.mock
async def test_get_current_weather_raises_on_error(client: WeatherClient) -> None:
    """API error codes raise an exception."""
    respx.get("https://api.qweather.com/v7/weather/now").mock(
        return_value=httpx.Response(200, json={"code": "401", "refer": []})
    )
    with pytest.raises(Exception, match="和风天气API错误"):
        await client.get_current_weather(location_id="101210101")


@respx.mock
async def test_search_city_by_name(client: WeatherClient) -> None:
    """City lookup returns the location_id."""
    respx.get("https://geoapi.qweather.com/v2/city/lookup").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": "200",
                "location": [
                    {
                        "id": "101210101",
                        "name": "杭州",
                        "adm2": "杭州",
                        "lat": "30.246",
                        "lon": "120.141",
                    }
                ],
            },
        )
    )
    result = await client.search_city("杭州")
    assert result["location_id"] == "101210101"
    assert result["name"] == "杭州"
