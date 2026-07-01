"""Amap API client tests."""

import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx

from mcp_servers.amap.client import AmapClient

MOCK_DIR = Path(__file__).parent.parent.parent / "data" / "mock"


@pytest.fixture
def mock_poi_response() -> dict[str, Any]:
    """Return a mocked Amap POI response."""
    return cast(
        dict[str, Any],
        json.loads((MOCK_DIR / "amap_poi_search.json").read_text(encoding="utf-8")),
    )


@pytest.fixture
def mock_route_response() -> dict[str, Any]:
    """Return a mocked Amap route response."""
    return cast(
        dict[str, Any],
        json.loads((MOCK_DIR / "amap_route.json").read_text(encoding="utf-8")),
    )


@pytest.fixture
def client() -> AmapClient:
    """Return an Amap test client."""
    return AmapClient(api_key="test-amap-key")


@respx.mock
async def test_search_attractions_returns_pois(
    client: AmapClient, mock_poi_response: dict[str, Any]
) -> None:
    """Searching attractions returns normalized POI results."""
    respx.get("https://restapi.amap.com/v3/place/text").mock(
        return_value=httpx.Response(200, json=mock_poi_response)
    )
    result = await client.search_attractions(city="杭州", keywords="西湖")

    assert len(result) == 2
    assert result[0]["name"] == "西湖"
    assert result[0]["location"] == "120.141,30.246"
    assert result[0]["rating"] == "4.8"


@respx.mock
async def test_search_attractions_handles_empty_response(client: AmapClient) -> None:
    """Empty API results return an empty list."""
    respx.get("https://restapi.amap.com/v3/place/text").mock(
        return_value=httpx.Response(200, json={"status": "1", "count": "0", "pois": []})
    )
    result = await client.search_attractions(city="杭州", keywords="不存在的景点")
    assert result == []


@respx.mock
async def test_search_attractions_raises_on_api_error(client: AmapClient) -> None:
    """Amap API errors raise a client exception."""
    respx.get("https://restapi.amap.com/v3/place/text").mock(
        return_value=httpx.Response(200, json={"status": "0", "info": "INVALID_USER_KEY"})
    )
    with pytest.raises(Exception, match="高德API错误"):
        await client.search_attractions(city="杭州", keywords="西湖")


@respx.mock
async def test_get_route_returns_distance_and_duration(
    client: AmapClient, mock_route_response: dict[str, Any]
) -> None:
    """Route planning returns distance in meters and duration in seconds."""
    respx.get("https://restapi.amap.com/v3/direction/walking").mock(
        return_value=httpx.Response(200, json=mock_route_response)
    )
    result = await client.get_route(
        origin="120.141,30.246", destination="120.087,30.233", mode="walking"
    )
    assert result["distance_m"] == 7820
    assert result["duration_s"] == 1234


@respx.mock
async def test_get_route_supports_driving(
    client: AmapClient, mock_route_response: dict[str, Any]
) -> None:
    """Driving mode calls the driving endpoint."""
    respx.get("https://restapi.amap.com/v3/direction/driving").mock(
        return_value=httpx.Response(200, json=mock_route_response)
    )
    result = await client.get_route(
        origin="120.141,30.246", destination="120.087,30.233", mode="driving"
    )
    assert result["distance_m"] == 7820
