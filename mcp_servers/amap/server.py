"""Amap MCP Server exposing travel-oriented map tools."""

import os
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.amap.client import AmapClient, AmapPoi, GeocodeResult, RouteMode, RouteResult

if TYPE_CHECKING:
    from collections.abc import Callable


def create_amap_server(api_key: str) -> FastMCP:
    """Create an Amap MCP Server instance."""
    server = FastMCP("amap-mcp-server")
    client = AmapClient(api_key=api_key)

    @server.tool()
    async def search_attractions(
        city: str, keywords: str = "", category: str = "", limit: int = 20
    ) -> list[AmapPoi]:
        """搜索指定城市的旅游景点。

        在规划旅游行程时, 用此工具查找目的地有哪些景点可去。
        支持按关键词或类别筛选。
        """
        return await client.search_attractions(
            city=city, keywords=keywords, category=category, limit=limit
        )

    @server.tool()
    async def get_route(origin: str, destination: str, mode: RouteMode = "walking") -> RouteResult:
        """查询两个地点之间的路线规划。

        在安排行程路线时, 用此工具计算景点之间的距离和交通时间,
        帮助优化游览顺序, 避免走回头路。
        """
        return await client.get_route(origin=origin, destination=destination, mode=mode)

    @server.tool()
    async def geocode(address: str, city: str = "") -> GeocodeResult:
        """将地址文本转换为经纬度坐标。

        当用户提到地名但需要计算路线时, 先用此工具获取经纬度。
        """
        return await client.geocode(address=address, city=city)

    @server.tool()
    async def search_nearby(
        location: str, radius: int = 1000, keywords: str = "", limit: int = 20
    ) -> list[AmapPoi]:
        """搜索指定位置附近的兴趣点（餐饮/景点/购物等）。

        当用户问"附近有什么好吃的""这附近有什么可以逛的"时, 用此工具。
        需要提供经纬度坐标。

        Args:
            location: 中心点经纬度 "lng,lat", 如 "120.123,30.234"
            radius: 搜索半径（米）, 默认1000
            keywords: 搜索关键词, 如 "餐饮""景点""小吃"
            limit: 返回数量上限, 默认20

        Returns:
            POI列表: name/location/address/tel/rating/cost
        """
        return await client.search_nearby(
            location=location, radius=radius, keywords=keywords, limit=limit
        )

    @server.tool()
    async def get_poi_detail(poi_id: str) -> dict[str, Any]:
        """获取某个地点的详细信息（营业时间/电话/评分/价格）。

        当用户问"这家店几点开门""门票多少钱"时用此工具。

        Args:
            poi_id: 高德POI ID（从 search_nearby 或 search_attractions 结果获取）

        Returns:
            name/address/tel/rating/cost/opentime
        """
        return await client.get_poi_detail(poi_id=poi_id)

    return server


def main() -> None:
    """Run the MCP server over stdio."""
    api_key = os.environ.get("AMAP_API_KEY", "")
    if not api_key:
        raise RuntimeError("AMAP_API_KEY 环境变量未设置")
    server = create_amap_server(api_key=api_key)
    run: Callable[[], None] = server.run
    run()


if __name__ == "__main__":
    main()
