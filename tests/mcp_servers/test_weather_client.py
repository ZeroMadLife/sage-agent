"""QWeather API client tests."""

import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx

from mcp_servers.weather.client import WeatherClient

MOCK_DIR = Path(__file__).parent.parent.parent / "data" / "mock"
TEST_QWEATHER_BASE_URL = "https://weather.test/v7"
TEST_QWEATHER_GEO_URL = "https://geo.test/geoapi/v2"


@pytest.fixture
def mock_now_response() -> dict[str, Any]:
    """Return a mocked current weather response."""
    return cast(
        dict[str, Any],
        json.loads((MOCK_DIR / "weather_now.json").read_text(encoding="utf-8")),
    )


@pytest.fixture
def mock_forecast_response() -> dict[str, Any]:
    """Return a mocked weather forecast response."""
    return cast(
        dict[str, Any],
        json.loads((MOCK_DIR / "weather_forecast.json").read_text(encoding="utf-8")),
    )


@pytest.fixture
def client() -> WeatherClient:
    """Return a QWeather test client."""
    return WeatherClient(
        api_key="test-weather-key",
        base_url=TEST_QWEATHER_BASE_URL,
        geo_url=TEST_QWEATHER_GEO_URL,
    )


@respx.mock
async def test_get_current_weather_returns_normalized(
    client: WeatherClient, mock_now_response: dict[str, Any]
) -> None:
    """Current weather returns a normalized structure."""
    respx.get(f"{TEST_QWEATHER_BASE_URL}/weather/now").mock(
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
    respx.get(f"{TEST_QWEATHER_BASE_URL}/weather/7d").mock(
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
    respx.get(f"{TEST_QWEATHER_BASE_URL}/weather/now").mock(
        return_value=httpx.Response(200, json={"code": "401", "refer": []})
    )
    with pytest.raises(Exception, match="和风天气API错误"):
        await client.get_current_weather(location_id="101210101")


@respx.mock
async def test_search_city_uses_local_map_first(client: WeatherClient) -> None:
    """已知城市优先走本地映射，不调 API。"""
    result = await client.search_city("杭州")
    assert result["location_id"] == "101210101"
    assert result["name"] == "杭州"


@respx.mock
async def test_search_city_uses_putian_local_map(client: WeatherClient) -> None:
    """莆田走本地 Location ID, 避免新版和风 host 的 geoapi 兼容问题。"""
    result = await client.search_city("莆田")
    assert result["location_id"] == "101230401"
    assert result["name"] == "莆田"


@respx.mock
async def test_search_city_fallback_to_api(client: WeatherClient) -> None:
    """本地映射找不到的城市 fallback 到 geoapi 接口。"""
    respx.get(f"{TEST_QWEATHER_GEO_URL}/city/lookup").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": "200",
                "location": [
                    {
                        "id": "101210401",
                        "name": "千岛湖",
                        "adm2": "淳安",
                        "lat": "29.608",
                        "lon": "119.032",
                    }
                ],
            },
        )
    )
    result = await client.search_city("千岛湖")
    assert result["location_id"] == "101210401"
    assert result["name"] == "千岛湖"
