"""MCP Server registry configuration."""

from typing import Literal, TypedDict


class McpServerConfig(TypedDict):
    """Config for one stdio MCP server."""

    command: str
    args: list[str]
    env: dict[str, str]
    transport: Literal["stdio"]


McpConfig = dict[str, McpServerConfig]


def build_mcp_config(
    amap_api_key: str,
    qweather_api_key: str,
    qweather_base_url: str = "",
    qweather_geo_url: str = "",
    scenic_data_path: str = "data/mock/scenic_spots.json",
) -> McpConfig:
    """Build MultiServerMCPClient-compatible stdio server config."""
    weather_env = {"QWEATHER_API_KEY": qweather_api_key}
    if qweather_base_url:
        weather_env["QWEATHER_BASE_URL"] = qweather_base_url
    if qweather_geo_url:
        weather_env["QWEATHER_GEO_URL"] = qweather_geo_url

    return {
        "amap": {
            "command": "python",
            "args": ["-m", "mcp_servers.amap.server"],
            "env": {"AMAP_API_KEY": amap_api_key},
            "transport": "stdio",
        },
        "weather": {
            "command": "python",
            "args": ["-m", "mcp_servers.weather.server"],
            "env": weather_env,
            "transport": "stdio",
        },
        "scenic": {
            "command": "python",
            "args": ["-m", "mcp_servers.scenic.server"],
            "env": {"SCENIC_DATA_PATH": scenic_data_path},
            "transport": "stdio",
        },
    }
