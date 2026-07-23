"""Bridge Sage MCP settings into scoped, credential-free Harness tools."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, cast

from langchain_core.tools import BaseTool, ToolException
from sage_harness import (
    McpCatalogPort,
    McpConfigSnapshot,
    McpConnectionStatus,
    McpManager,
    McpScope,
    McpServerConfig,
    McpServerReference,
    McpToolDescriptor,
)

from core.coding.run_coordinator import RunEvent
from core.config.settings import Settings
from core.harness.mcp_session_pool import McpClientSession, ScopedMcpSessionPool
from core.mcp_client import build_config_from_settings
from mcp_servers.registry import McpConfig

McpClientFactory = Callable[[dict[str, Any]], Any]
McpToolLoader = Callable[[McpClientSession, str], Awaitable[Sequence[BaseTool]]]


class ConfiguredMcpCatalog(McpCatalogPort):
    """Read-only view over MCP configuration; it never opens a connection."""

    def __init__(
        self,
        config: McpConfig,
        *,
        statuses: Mapping[str, McpConnectionStatus] | None = None,
    ) -> None:
        status_by_name = dict(statuses or {})
        self._servers = tuple(
            McpServerReference(
                name=name,
                transport=str(spec.get("transport", "stdio")),
                status=status_by_name.get(name, "configured"),
            )
            for name, spec in sorted(config.items())
        )

    async def list_servers(self) -> Sequence[McpServerReference]:
        return self._servers


class LangChainMcpTransport:
    """Keep raw connection settings and discovered tools behind the app boundary."""

    def __init__(
        self,
        config: McpConfig,
        *,
        revision: str,
        client_factory: McpClientFactory | None = None,
        session_pool: ScopedMcpSessionPool | None = None,
        tool_loader: McpToolLoader | None = None,
    ) -> None:
        self._connections = {name: dict(connection) for name, connection in config.items()}
        self._revision = revision
        self._client_factory = client_factory
        self._session_pool = session_pool or ScopedMcpSessionPool()
        self._tool_loader = tool_loader or _load_tools_from_session
        self._server_configs: dict[str, McpServerConfig] = {}
        self._tools: dict[
            tuple[str, tuple[str, str, str], str],
            dict[str, BaseTool],
        ] = {}

    async def discover(
        self,
        server: McpServerConfig,
        scope: McpScope,
    ) -> Sequence[McpToolDescriptor]:
        connection = self._connections.get(server.name)
        if connection is None:
            raise ValueError("MCP server connection is unavailable")
        self._server_configs[server.name] = server
        if self._client_factory is not None:
            client = self._client_factory({server.name: connection})
            discovered = await client.get_tools(server_name=server.name)
        else:
            session = await self._session_pool.get_session(
                revision=self._revision,
                server_name=server.name,
                scope=scope,
                connection=connection,
            )
            discovered = await self._tool_loader(session, server.name)
        key = (self._revision, scope.key, server.name)
        tools_by_id: dict[str, BaseTool] = {}
        descriptors: list[McpToolDescriptor] = []
        prefix = f"{server.name}_"
        for tool in discovered:
            if not isinstance(tool, BaseTool):
                raise TypeError("MCP discovery returned a non-tool value")
            if not tool.name.startswith(prefix):
                raise ValueError("MCP adapter did not apply the required server prefix")
            original_name = tool.name[len(prefix) :]
            tool_id = f"{server.name}:{original_name}"
            descriptor = McpToolDescriptor.from_schema(
                tool_id=tool_id,
                server_name=server.name,
                name=tool.name,
                original_name=original_name,
                description=str(tool.description or "MCP tool"),
                schema=_tool_schema(tool),
                remote_content=server.remote_content,
            )
            tools_by_id[tool_id] = tool
            descriptors.append(descriptor)
        self._tools[key] = tools_by_id
        return descriptors

    async def invoke(
        self,
        tool: McpToolDescriptor,
        arguments: Mapping[str, object],
        scope: McpScope,
    ) -> object:
        key = (self._revision, scope.key, tool.server_name)
        discovered = self._tools.get(key)
        if self._client_factory is None and not self._session_pool.has_session(
            revision=self._revision,
            server_name=tool.server_name,
            scope=scope,
        ):
            self._tools.pop(key, None)
            discovered = None
        if discovered is None or tool.tool_id not in discovered:
            server = self._server_configs.get(tool.server_name)
            if server is None:
                raise RuntimeError("MCP tool is stale or was not discovered for this scope")
            await self.discover(server, scope)
            discovered = self._tools.get(key)
        if discovered is None or tool.tool_id not in discovered:
            raise RuntimeError("MCP tool is unavailable after reconnecting")
        try:
            return await discovered[tool.tool_id].ainvoke(dict(arguments))
        except ToolException:
            # A server-declared tool error is data, not proof that the transport
            # died; do not reconnect or risk replaying a side-effecting call.
            raise
        except Exception:
            await self._session_pool.close_session(
                revision=self._revision,
                server_name=tool.server_name,
                scope=scope,
            )
            self._tools.pop(key, None)
            raise

    async def close_scope(self, scope: McpScope) -> None:
        keys = [key for key in self._tools if key[1] == scope.key]
        for key in keys:
            self._tools.pop(key, None)
        await self._session_pool.close_scope(scope)

    async def invalidate_revision(self, revision: str) -> None:
        keys = [key for key in self._tools if key[0] == revision]
        for key in keys:
            self._tools.pop(key, None)
        await self._session_pool.invalidate_revision(revision)

    async def aclose(self) -> None:
        self._tools.clear()
        await self._session_pool.aclose()


def build_configured_mcp_catalog(
    settings: Settings,
    *,
    scenic_data_path: str = "data/mock/scenic_spots.json",
) -> ConfiguredMcpCatalog:
    """Build a sanitized catalog and distinguish configured from missing credentials."""
    config = build_config_from_settings(settings, scenic_data_path=scenic_data_path)
    statuses: dict[str, McpConnectionStatus] = {
        "amap": "configured" if settings.amap_api_key.strip() else "unconfigured",
        "weather": "configured" if settings.qweather_api_key.strip() else "unconfigured",
        "scenic": "configured" if scenic_data_path.strip() else "unconfigured",
    }
    return ConfiguredMcpCatalog(config, statuses=statuses)


def build_configured_mcp_manager(
    settings: Settings,
    *,
    scenic_data_path: str = "data/mock/scenic_spots.json",
    client_factory: McpClientFactory | None = None,
    discovery_timeout_seconds: float = 10.0,
    call_timeout_seconds: float = 30.0,
) -> McpManager:
    """Build a lazy live manager; construction never opens an MCP connection."""
    config = build_config_from_settings(settings, scenic_data_path=scenic_data_path)
    configured = {
        "amap": bool(settings.amap_api_key.strip()),
        "weather": bool(settings.qweather_api_key.strip()),
        "scenic": bool(scenic_data_path.strip()),
    }
    revision = _config_revision(config, configured)
    servers = tuple(
        McpServerConfig(
            name=name,
            transport=cast(Any, spec.get("transport", "stdio")),
            status="configured" if configured.get(name, False) else "unconfigured",
            remote_content=name != "scenic",
            capabilities=frozenset({"network"}) if name != "scenic" else frozenset({"local_data"}),
        )
        for name, spec in sorted(config.items())
    )
    transport = LangChainMcpTransport(
        {name: spec for name, spec in config.items() if configured.get(name, False)},
        revision=revision,
        client_factory=client_factory,
    )
    return McpManager(
        McpConfigSnapshot(revision=revision, servers=servers),
        transport,
        discovery_timeout_seconds=discovery_timeout_seconds,
        call_timeout_seconds=call_timeout_seconds,
    )


async def mcp_catalog_event(
    catalog: McpCatalogPort,
    *,
    session_id: str,
    run_id: str,
    servers: Sequence[McpServerReference | McpServerConfig] | None = None,
) -> RunEvent:
    """Project only bounded server metadata into the durable public timeline."""
    public_servers = tuple(servers) if servers is not None else await catalog.list_servers()
    return RunEvent(
        kind="harness",
        status="completed",
        payload={
            "type": "mcp_catalog_updated",
            "runtime_profile": "deerflow_v2",
            "session_id": session_id,
            "run_id": run_id,
            "servers": [
                {
                    "name": server.name,
                    "transport": server.transport,
                    "status": server.status,
                    "tool_names": list(getattr(server, "tool_names", ())),
                }
                for server in public_servers
            ],
        },
        event_id=f"harness:{run_id}:mcp-catalog",
    )


def _default_mcp_client(connections: dict[str, Any]) -> Any:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    return MultiServerMCPClient(connections, tool_name_prefix=True)


async def _load_tools_from_session(
    session: McpClientSession,
    server_name: str,
) -> Sequence[BaseTool]:
    from langchain_mcp_adapters.tools import load_mcp_tools

    return await load_mcp_tools(
        cast(Any, session),
        server_name=server_name,
        tool_name_prefix=True,
    )


def _tool_schema(tool: BaseTool) -> dict[str, object]:
    schema = tool.args_schema
    if isinstance(schema, dict):
        return {str(key): value for key, value in schema.items()}
    if schema is not None and hasattr(schema, "model_json_schema"):
        rendered = schema.model_json_schema()
        if isinstance(rendered, dict):
            return {str(key): value for key, value in rendered.items()}
    rendered = cast(Any, tool.get_input_schema()).model_json_schema()
    return {str(key): value for key, value in rendered.items()}


def _config_revision(config: McpConfig, configured: Mapping[str, bool]) -> str:
    payload = {
        "config": config,
        "configured": dict(sorted(configured.items())),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "ConfiguredMcpCatalog",
    "LangChainMcpTransport",
    "build_configured_mcp_catalog",
    "build_configured_mcp_manager",
    "mcp_catalog_event",
]
