"""Helpers for constructing MCP client configuration."""

from core.config.settings import Settings
from mcp_servers.registry import McpConfig, build_mcp_config


def build_config_from_settings(
    settings: Settings, scenic_data_path: str = "data/mock/scenic_spots.json"
) -> McpConfig:
    """Build MCP server configuration from application settings."""
    return build_mcp_config(
        amap_api_key=settings.amap_api_key,
        qweather_api_key=settings.qweather_api_key,
        scenic_data_path=scenic_data_path,
    )
