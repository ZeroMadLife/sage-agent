"""MCP Server registry config tests."""

from mcp_servers.registry import build_mcp_config


def test_build_mcp_config_includes_all_three_servers() -> None:
    """Registry config includes all three MCP servers."""
    config = build_mcp_config(
        amap_api_key="test-amap",
        qweather_api_key="test-weather",
        scenic_data_path="data/mock/scenic_spots.json",
    )
    assert "amap" in config
    assert "weather" in config
    assert "scenic" in config


def test_amap_config_has_command_and_env() -> None:
    """Amap server config has command and env."""
    config = build_mcp_config(
        amap_api_key="test-amap",
        qweather_api_key="test-weather",
        scenic_data_path="data/mock/scenic_spots.json",
    )
    amap = config["amap"]
    assert amap["command"] == "python"
    assert "-m" in amap["args"]
    assert "mcp_servers.amap.server" in amap["args"]
    assert amap["env"]["AMAP_API_KEY"] == "test-amap"


def test_weather_config_has_correct_module() -> None:
    """Weather server config points at the correct module."""
    config = build_mcp_config(
        amap_api_key="test-amap",
        qweather_api_key="test-weather",
        scenic_data_path="data/mock/scenic_spots.json",
    )
    weather = config["weather"]
    assert "mcp_servers.weather.server" in weather["args"]
    assert weather["env"]["QWEATHER_API_KEY"] == "test-weather"


def test_scenic_config_has_data_path() -> None:
    """Scenic server config contains the scenic data path."""
    config = build_mcp_config(
        amap_api_key="test-amap",
        qweather_api_key="test-weather",
        scenic_data_path="data/mock/scenic_spots.json",
    )
    scenic = config["scenic"]
    assert scenic["env"]["SCENIC_DATA_PATH"] == "data/mock/scenic_spots.json"
