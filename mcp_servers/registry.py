"""MCP Server registry configuration."""

import sys
from pathlib import Path
from typing import Literal, TypedDict


class McpServerConfig(TypedDict):
    """Config for one stdio MCP server."""

    command: str
    args: list[str]
    env: dict[str, str]
    transport: Literal["stdio"]
    cwd: str


McpConfig = dict[str, McpServerConfig]


def build_mcp_config(
    amap_api_key: str,
    qweather_api_key: str,
    qweather_base_url: str = "",
    qweather_geo_url: str = "",
    scenic_data_path: str = "data/mock/scenic_spots.json",
) -> McpConfig:
    """Build MultiServerMCPClient-compatible stdio server config."""
    repo_root = str(Path(__file__).resolve().parent.parent)
    weather_env = {"QWEATHER_API_KEY": qweather_api_key}
    if qweather_base_url:
        weather_env["QWEATHER_BASE_URL"] = qweather_base_url
    if qweather_geo_url:
        weather_env["QWEATHER_GEO_URL"] = qweather_geo_url

    return {
        "amap": {
            "command": sys.executable,
            "args": ["-m", "mcp_servers.amap.server"],
            "env": {"AMAP_API_KEY": amap_api_key},
            "transport": "stdio",
            "cwd": repo_root,
        },
        "weather": {
            "command": sys.executable,
            "args": ["-m", "mcp_servers.weather.server"],
            "env": weather_env,
            "transport": "stdio",
            "cwd": repo_root,
        },
        "scenic": {
            "command": sys.executable,
            "args": ["-m", "mcp_servers.scenic.server"],
            "env": {"SCENIC_DATA_PATH": scenic_data_path},
            "transport": "stdio",
            "cwd": repo_root,
        },
    }
