"""MCP client configuration bridge tests."""

from core.config.settings import Settings
from core.mcp_client import build_config_from_settings


def test_build_config_from_settings_passes_qweather_urls() -> None:
    """Settings-derived MCP config includes QWeather API hosts."""
    settings = Settings(
        amap_api_key="test-amap",
        qweather_api_key="test-weather",
        qweather_base_url="https://weather.test/v7",
        qweather_geo_url="https://geo.test/geoapi/v2",
    )

    config = build_config_from_settings(settings, scenic_data_path="data/mock/scenic_spots.json")

    weather_env = config["weather"]["env"]
    assert weather_env["QWEATHER_API_KEY"] == "test-weather"
    assert weather_env["QWEATHER_BASE_URL"] == "https://weather.test/v7"
    assert weather_env["QWEATHER_GEO_URL"] == "https://geo.test/geoapi/v2"
