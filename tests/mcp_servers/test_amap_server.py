"""Amap MCP Server tests."""

from typing import Any, cast
from unittest.mock import AsyncMock, patch

from mcp_servers.amap.server import create_amap_server


def _tools(server: Any) -> dict[str, Any]:
    return cast(dict[str, Any], server._tool_manager._tools)


def test_server_exposes_search_attractions_tool() -> None:
    """Server exposes the search_attractions tool."""
    server = create_amap_server(api_key="test-key")
    assert "search_attractions" in _tools(server)


def test_server_exposes_get_route_tool() -> None:
    """Server exposes the get_route tool."""
    server = create_amap_server(api_key="test-key")
    assert "get_route" in _tools(server)


def test_server_exposes_geocode_tool() -> None:
    """Server exposes the geocode tool."""
    server = create_amap_server(api_key="test-key")
    assert "geocode" in _tools(server)


def test_search_attractions_tool_has_correct_schema() -> None:
    """search_attractions has the expected name and useful description."""
    server = create_amap_server(api_key="test-key")
    tool = _tools(server)["search_attractions"]
    assert tool.name == "search_attractions"
    assert "城市" in tool.description or "景点" in tool.description


async def test_search_attractions_tool_delegates_to_client() -> None:
    """search_attractions delegates to AmapClient."""
    with patch("mcp_servers.amap.server.AmapClient") as client_class:
        client = client_class.return_value
        client.search_attractions = AsyncMock(return_value=[{"name": "西湖"}])

        server = create_amap_server(api_key="test-key")
        result = await _tools(server)["search_attractions"].run(
            {"city": "杭州", "keywords": "西湖", "category": "", "limit": 10}
        )

    assert result == [{"name": "西湖"}]
    client.search_attractions.assert_awaited_once_with(
        city="杭州", keywords="西湖", category="", limit=10
    )


async def test_get_route_tool_delegates_to_client() -> None:
    """get_route delegates to AmapClient."""
    route = {"distance_m": 100, "duration_s": 60, "steps": []}
    with patch("mcp_servers.amap.server.AmapClient") as client_class:
        client = client_class.return_value
        client.get_route = AsyncMock(return_value=route)

        server = create_amap_server(api_key="test-key")
        result = await _tools(server)["get_route"].run(
            {"origin": "120.141,30.246", "destination": "120.087,30.233", "mode": "walking"}
        )

    assert result == route
    client.get_route.assert_awaited_once_with(
        origin="120.141,30.246", destination="120.087,30.233", mode="walking"
    )


async def test_geocode_tool_delegates_to_client() -> None:
    """geocode delegates to AmapClient."""
    geocode = {"location": "120.141,30.246", "formatted_address": "西湖", "level": "景点"}
    with patch("mcp_servers.amap.server.AmapClient") as client_class:
        client = client_class.return_value
        client.geocode = AsyncMock(return_value=geocode)

        server = create_amap_server(api_key="test-key")
        result = await _tools(server)["geocode"].run({"address": "西湖", "city": "杭州"})

    assert result == geocode
    client.geocode.assert_awaited_once_with(address="西湖", city="杭州")


def test_server_exposes_search_nearby_tool() -> None:
    """Server exposes the search_nearby tool."""
    server = create_amap_server(api_key="test-key")
    assert "search_nearby" in _tools(server)


def test_server_exposes_get_poi_detail_tool() -> None:
    """Server exposes the get_poi_detail tool."""
    server = create_amap_server(api_key="test-key")
    assert "get_poi_detail" in _tools(server)
