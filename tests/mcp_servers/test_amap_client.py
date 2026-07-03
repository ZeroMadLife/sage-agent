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


@respx.mock
async def test_search_nearby_returns_pois(client: AmapClient) -> None:
    """周边搜索返回标准化POI列表。"""
    respx.get("https://restapi.amap.com/v3/place/around").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "pois": [
                    {
                        "id": "B001",
                        "name": "沙县小吃",
                        "type": "餐饮服务;快餐厅",
                        "address": "学林街",
                        "location": "120.123,30.234",
                        "tel": "13800001111",
                        "biz_ext": {"rating": "4.2", "cost": "15"},
                    },
                ],
            },
        )
    )
    result = await client.search_nearby(
        location="120.123,30.234", radius=1000, keywords="餐饮", limit=10
    )
    assert len(result) == 1
    assert result[0]["name"] == "沙县小吃"
    assert result[0]["location"] == "120.123,30.234"


@respx.mock
async def test_search_nearby_handles_empty(client: AmapClient) -> None:
    """周边搜索无结果时返回空列表。"""
    respx.get("https://restapi.amap.com/v3/place/around").mock(
        return_value=httpx.Response(200, json={"status": "1", "pois": []})
    )
    result = await client.search_nearby(location="0,0", radius=500)
    assert result == []


@respx.mock
async def test_get_poi_detail_returns_full_info(client: AmapClient) -> None:
    """POI详情返回完整信息。"""
    respx.get("https://restapi.amap.com/v3/place/detail").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "pois": [
                    {
                        "id": "B001",
                        "name": "沙县小吃",
                        "address": "学林街123号",
                        "location": "120.123,30.234",
                        "tel": "13800001111",
                        "type": "餐饮服务;快餐厅",
                        "biz_ext": {"rating": "4.2", "cost": "15"},
                        "opentime": "07:00-22:00",
                    }
                ],
            },
        )
    )
    result = await client.get_poi_detail(poi_id="B001")
    assert result["name"] == "沙县小吃"
    assert result["opentime"] == "07:00-22:00"
