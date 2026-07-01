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
    scenic_data_path: str = "data/mock/scenic_spots.json",
) -> McpConfig:
    """Build MultiServerMCPClient-compatible stdio server config."""
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
            "env": {"QWEATHER_API_KEY": qweather_api_key},
            "transport": "stdio",
        },
        "scenic": {
            "command": "python",
            "args": ["-m", "mcp_servers.scenic.server"],
            "env": {"SCENIC_DATA_PATH": scenic_data_path},
            "transport": "stdio",
        },
    }
