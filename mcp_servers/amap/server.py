"""Amap MCP Server exposing travel-oriented map tools."""

import os
from typing import TYPE_CHECKING

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
