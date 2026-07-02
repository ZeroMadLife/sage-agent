"""Scenic information MCP Server."""

import os
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from mcp_servers.scenic.client import ScenicClient, ScenicSpot, ScenicSpotSummary

if TYPE_CHECKING:
    from collections.abc import Callable


def create_scenic_server(data_path: str) -> FastMCP:
    """Create a Scenic MCP Server instance."""
    server = FastMCP("scenic-mcp-server")
    client = ScenicClient(data_path=data_path)

    @server.tool()
    def search_scenic_spots(
        city: str = "",
        category: str = "",
        keywords: str = "",
        free_only: bool = False,
        limit: int = 20,
    ) -> list[ScenicSpotSummary]:
        """搜索旅游景点。

        在规划行程时, 用此工具查找目的地有哪些景点可去。
        支持按城市、类别、关键词筛选, 也可筛选免费景点。
        """
        return client.search_scenic_spots(
            city=city,
            category=category,
            keywords=keywords,
            free_only=free_only,
            limit=limit,
        )

    @server.tool()
    def get_scenic_detail(spot_id: str) -> ScenicSpot | None:
        """获取景点详细信息。

        在确定行程中要去的景点后, 用此工具获取开放时间、门票价格、
        建议游览时长等详情, 用于行程时间安排和预算计算。
        """
        return client.get_scenic_detail(spot_id)

    @server.tool()
    def get_opening_hours(spot_id: str) -> str:
        """查询景点开放时间。"""
        return client.get_opening_hours(spot_id) or "未知"

    @server.tool()
    def get_ticket_price(spot_id: str) -> int:
        """查询景点门票价格。

        在预算Agent计算行程花费时, 用此工具获取景点门票价格。
        """
        return client.get_ticket_price(spot_id) or 0

    return server


def main() -> None:
    """Run the MCP server over stdio."""
    data_path = os.environ.get("SCENIC_DATA_PATH", "data/mock/scenic_spots.json")
    server = create_scenic_server(data_path=data_path)
    run: Callable[[], None] = server.run
    run()


if __name__ == "__main__":
    main()
