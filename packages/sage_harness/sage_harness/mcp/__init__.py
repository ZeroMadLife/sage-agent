"""Application-neutral MCP manager contracts."""

from sage_harness.mcp.manager import (
    McpCatalogSnapshot,
    McpConfigSnapshot,
    McpLifecycleError,
    McpLifecycleSnapshot,
    McpManager,
    McpScope,
    McpServerConfig,
    McpToolDescriptor,
    McpToolSnapshot,
    McpTransportPort,
)

__all__ = [
    "McpCatalogSnapshot",
    "McpConfigSnapshot",
    "McpLifecycleError",
    "McpLifecycleSnapshot",
    "McpManager",
    "McpScope",
    "McpServerConfig",
    "McpToolDescriptor",
    "McpToolSnapshot",
    "McpTransportPort",
]
