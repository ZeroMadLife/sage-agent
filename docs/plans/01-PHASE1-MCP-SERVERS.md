# Phase 1：环境搭建与 MCP Server 开发 — TDD 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 3个自研MCP Server（高德地图/天气/景点）可独立调用，通过单元测试，在MCP Inspector中可验证。

**Architecture:** 每个MCP Server用 Python MCP SDK 的 `FastMCP` 类实现，通过 stdio 传输。工具设计遵循 Outcome-oriented 原则（按业务结果设计，非底层API操作）。外部API调用通过 httpx 异步客户端，开发测试用 respx Mock。

**Tech Stack:** Python 3.11+ / mcp SDK / httpx / pydantic / pytest / respx / mypy / ruff

**类型安全约定：** 所有函数必须标注 type hints；所有数据模型用 Pydantic；每个 Task 完成后跑 `mypy` + `ruff check` 确保类型安全。这弥补 Python 动态类型的短板，让 AI 复审和 code review 更可靠。

**预计耗时：** Week 1-2（10个工作日）

---

## 文件结构

Phase 1 完成后的目录结构：

```
tour-agent/
├── core/
│   ├── __init__.py
│   └── config/
│       ├── __init__.py
│       └── settings.py          # pydantic-settings 全局配置
├── mcp_servers/
│   ├── __init__.py
│   ├── amap/
│   │   ├── __init__.py
│   │   ├── server.py            # 高德地图 MCP Server
│   │   └── client.py            # 高德 API 异步客户端
│   ├── weather/
│   │   ├── __init__.py
│   │   ├── server.py            # 天气 MCP Server
│   │   └── client.py            # 和风天气 API 异步客户端
│   └── scenic/
│       ├── __init__.py
│       ├── server.py            # 景点 MCP Server
│       └── client.py            # 景点数据客户端（本地DB + 高德POI）
├── data/
│   └── mock/
│       ├── amap_poi.json        # 高德POI Mock响应
│       ├── amap_route.json      # 高德路线 Mock响应
│       ├── weather_now.json     # 天气 Mock响应
│       └── scenic_spots.json    # 景点 Mock数据
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # pytest fixtures
│   └── mcp_servers/
│       ├── __init__.py
│       ├── test_amap_server.py
│       ├── test_weather_server.py
│       └── test_scenic_server.py
├── .env.example
├── docker-compose.yml
├── requirements.txt
└── pytest.ini
```

---

## Task 1：配置管理模块

**Files:**
- Create: `core/__init__.py`
- Create: `core/config/__init__.py`
- Create: `core/config/settings.py`
- Create: `tests/conftest.py`
- Create: `pytest.ini`

- [ ] **Step 1: 创建 pytest 配置**

创建 `pytest.ini`：

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
python_functions = test_*
addopts = -v --tb=short
```

- [ ] **Step 2: 创建 __init__.py 文件**

```bash
touch core/__init__.py core/config/__init__.py tests/__init__.py tests/mcp_servers/__init__.py
```

- [ ] **Step 3: 写配置模块的失败测试**

创建 `tests/conftest.py`：

```python
"""Pytest 全局 fixtures。"""
import os
import pytest


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    """为所有测试注入测试环境变量。"""
    test_env = {
        "AMAP_API_KEY": "test-amap-key",
        "QWEATHER_API_KEY": "test-weather-key",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "test",
        "POSTGRES_PASSWORD": "test",
        "POSTGRES_DB": "test",
        "REDIS_HOST": "localhost",
        "REDIS_PORT": "6379",
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
        "LLM_MODEL": "gpt-4o-mini",
        "APP_ENV": "test",
    }
    for key, value in test_env.items():
        monkeypatch.setenv(key, value)
```

创建 `tests/core/__init__.py` 和 `tests/core/test_config.py`：

```bash
mkdir -p tests/core && touch tests/core/__init__.py
```

```python
"""配置模块测试。"""
from core.config.settings import Settings


def test_settings_loads_from_env():
    """配置能从环境变量加载。"""
    settings = Settings()
    assert settings.amap_api_key == "test-amap-key"
    assert settings.qweather_api_key == "test-weather-key"


def test_settings_has_amap_base_url():
    """高德API基础URL有默认值。"""
    settings = Settings()
    assert "restapi.amap.com" in settings.amap_base_url


def test_settings_has_qweather_base_url():
    """和风天气API基础URL有默认值。"""
    settings = Settings()
    assert "api.qweather.com" in settings.qweather_base_url
```

- [ ] **Step 4: 运行测试确认失败**

Run: `pytest tests/core/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.config.settings'`

- [ ] **Step 5: 实现配置模块**

创建 `core/config/settings.py`：

```python
"""TourSwarm 全局配置。

使用 pydantic-settings 从环境变量 / .env 文件加载配置。
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- 第三方 API ----------
    amap_api_key: str = Field(description="高德地图 Web 服务 Key")
    amap_base_url: str = "https://restapi.amap.com/v3"

    qweather_api_key: str = Field(description="和风天气 API Key")
    qweather_base_url: str = "https://api.qweather.com/v7"
    qweather_geo_url: str = "https://geoapi.qweather.com/v2"

    caiyun_api_key: str = ""

    # ---------- LLM ----------
    llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_light_model: str = "gpt-4o-mini"

    # ---------- 数据库 ----------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "tourswarm"
    postgres_password: str = "tourswarm_dev"
    postgres_db: str = "tourswarm"

    # ---------- Redis ----------
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # ---------- Qdrant ----------
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333

    # ---------- 应用 ----------
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_secret_key: str = "change-me-in-production"

    # ---------- 可观测性 ----------
    langsmith_api_key: str = ""
    langsmith_project: str = "tourswarm"

    # ---------- Mem0 ----------
    mem0_vector_store: str = "qdrant"
    mem0_embedder_model: str = "BAAI/bge-large-zh-v1.5"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


def get_settings() -> Settings:
    """获取全局 Settings 单例（带 lru_cache 可在测试中 override）。"""
    return Settings()
```

- [ ] **Step 6: 运行测试确认通过**

Run: `pytest tests/core/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: 类型检查 + lint**

```bash
mypy core/config/settings.py
ruff check core/config/settings.py tests/core/test_config.py
```
Expected: mypy 无错误，ruff 无违规。这是每个Task的强制步骤——弥补Python动态类型短板。

- [ ] **Step 8: Commit**

```bash
git add core/ tests/conftest.py tests/core/ pytest.ini pyproject.toml ruff.toml
git commit -m "feat: add config management module with pydantic-settings"
```

---

## Task 2：高德地图 API 客户端

**Files:**
- Create: `mcp_servers/__init__.py`
- Create: `mcp_servers/amap/__init__.py`
- Create: `mcp_servers/amap/client.py`
- Create: `data/mock/amap_poi_search.json`
- Create: `data/mock/amap_route.json`
- Test: `tests/mcp_servers/test_amap_client.py`

- [ ] **Step 1: 创建 Mock 数据**

创建 `data/mock/amap_poi_search.json`：

```json
{
  "status": "1",
  "count": "2",
  "pois": [
    {
      "id": "B0FFHPRXHN",
      "name": "西湖",
      "type": "风景名胜:风景名胜相关",
      "address": "龙井路1号",
      "location": "120.141,30.246",
      "tel": "0571-87969691",
      "tag": "自然风光",
      "biz_ext": {"rating": "4.8", "cost": "0"}
    },
    {
      "id": "B0FFHUVYWX",
      "name": "灵隐寺",
      "type": "风景名胜:寺庙",
      "address": "灵隐路法云弄1号",
      "location": "120.087,30.233",
      "tel": "0571-87968665",
      "tag": "人文古迹",
      "biz_ext": {"rating": "4.7", "cost": "30"}
    }
  ]
}
```

创建 `data/mock/amap_route.json`：

```json
{
  "status": "1",
  "route": {
    "origin": "120.141,30.246",
    "destination": "120.087,30.233",
    "paths": [
      {
        "distance": "7820",
        "duration": "1234",
        "steps": [
          {"instruction": "沿龙井路向西行驶", "distance": "3200", "duration": "480"}
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: 写失败测试**

创建 `tests/mcp_servers/test_amap_client.py`：

```python
"""高德地图 API 客户端测试。"""
import json
from pathlib import Path

import httpx
import pytest
import respx

from mcp_servers.amap.client import AmapClient

MOCK_DIR = Path(__file__).parent.parent.parent / "data" / "mock"


@pytest.fixture
def mock_poi_response():
    return json.loads((MOCK_DIR / "amap_poi_search.json").read_text())


@pytest.fixture
def mock_route_response():
    return json.loads((MOCK_DIR / "amap_route.json").read_text())


@pytest.fixture
def client():
    return AmapClient(api_key="test-amap-key")


@respx.mock
async def test_search_attractions_returns_pois(client, mock_poi_response):
    """搜索景点应返回标准化POI列表。"""
    respx.get("https://restapi.amap.com/v3/place/text").mock(
        return_value=httpx.Response(200, json=mock_poi_response)
    )
    result = await client.search_attractions(city="杭州", keywords="西湖")

    assert len(result) == 2
    assert result[0]["name"] == "西湖"
    assert result[0]["location"] == "120.141,30.246"
    assert result[0]["rating"] == "4.8"


@respx.mock
async def test_search_attractions_handles_empty_response(client):
    """API返回空结果时应返回空列表。"""
    respx.get("https://restapi.amap.com/v3/place/text").mock(
        return_value=httpx.Response(200, json={"status": "1", "count": "0", "pois": []})
    )
    result = await client.search_attractions(city="杭州", keywords="不存在的景点")
    assert result == []


@respx.mock
async def test_search_attractions_raises_on_api_error(client):
    """API返回错误状态时应抛出异常。"""
    respx.get("https://restapi.amap.com/v3/place/text").mock(
        return_value=httpx.Response(200, json={"status": "0", "info": "INVALID_USER_KEY"})
    )
    with pytest.raises(Exception, match="高德API错误"):
        await client.search_attractions(city="杭州", keywords="西湖")


@respx.mock
async def test_get_route_returns_distance_and_duration(client, mock_route_response):
    """路线规划应返回距离（米）和时长（秒）。"""
    respx.get("https://restapi.amap.com/v3/direction/walking").mock(
        return_value=httpx.Response(200, json=mock_route_response)
    )
    result = await client.get_route(
        origin="120.141,30.246", destination="120.087,30.233", mode="walking"
    )
    assert result["distance_m"] == 7820
    assert result["duration_s"] == 1234


@respx.mock
async def test_get_route_supports_driving(client, mock_route_response):
    """驾车模式应调用正确的endpoint。"""
    respx.get("https://restapi.amap.com/v3/direction/driving").mock(
        return_value=httpx.Response(200, json=mock_route_response)
    )
    result = await client.get_route(
        origin="120.141,30.246", destination="120.087,30.233", mode="driving"
    )
    assert result["distance_m"] == 7820
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/mcp_servers/test_amap_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'AmapClient'`

- [ ] **Step 4: 实现 AmapClient**

创建 `mcp_servers/amap/client.py`：

```python
"""高德地图 API 异步客户端。

封装高德 Web 服务 API，提供景点搜索和路线规划能力。
所有方法返回标准化的 dict / list，屏蔽高德原始响应结构差异。
"""
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


class AmapClientError(Exception):
    """高德 API 调用异常。"""


class AmapClient:
    """高德地图 API 异步客户端。"""

    def __init__(self, api_key: str, base_url: str = "https://restapi.amap.com/v3"):
        self._api_key = api_key
        self._base_url = base_url
        self._http = httpx.AsyncClient(timeout=10.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    async def search_attractions(
        self, city: str, keywords: str = "", category: str = "", limit: int = 20
    ) -> list[dict]:
        """搜索景点POI，返回标准化列表。

        Args:
            city: 城市名（如"杭州"）
            keywords: 搜索关键词
            category: 类别过滤（如"自然风光""人文古迹"）
            limit: 返回数量上限

        Returns:
            标准化POI列表，每项含 name/location/address/tel/rating/cost/tag
        """
        params = {
            "key": self._api_key,
            "keywords": keywords or category or "景点",
            "city": city,
            "citylimit": "true",
            "types": "110000",  # 风景名胜
            "offset": str(limit),
            "page": "1",
            "extensions": "all",
        }
        resp = await self._http.get(f"{self._base_url}/place/text", params=params)
        data = resp.json()

        if data.get("status") != "1":
            raise AmapClientError(f"高德API错误: {data.get('info', 'unknown')}")

        pois = data.get("pois", [])
        return [self._normalize_poi(p) for p in pois]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    async def get_route(
        self, origin: str, destination: str, mode: str = "walking"
    ) -> dict:
        """路线规划，返回距离和时长。

        Args:
            origin: 起点经纬度 "lng,lat"
            destination: 终点经纬度 "lng,lat"
            mode: walking / driving / transit

        Returns:
            {"distance_m": int, "duration_s": int, "steps": [...]}
        """
        endpoint_map = {
            "walking": "/direction/walking",
            "driving": "/direction/driving",
            "transit": "/direction/transit/integrated",
        }
        endpoint = endpoint_map.get(mode, "/direction/walking")
        params = {
            "key": self._api_key,
            "origin": origin,
            "destination": destination,
        }
        if mode == "transit":
            params["city"] = "杭州"  # transit 需要城市参数，由调用方覆盖

        resp = await self._http.get(f"{self._base_url}{endpoint}", params=params)
        data = resp.json()

        if data.get("status") != "1":
            raise AmapClientError(f"高德API错误: {data.get('info', 'unknown')}")

        route = data.get("route", {})
        paths = route.get("paths", [{}])
        if not paths:
            return {"distance_m": 0, "duration_s": 0, "steps": []}

        path = paths[0]
        return {
            "distance_m": int(path.get("distance", 0)),
            "duration_s": int(path.get("duration", 0)),
            "steps": [
                {
                    "instruction": s.get("instruction", ""),
                    "distance_m": int(s.get("distance", 0)),
                    "duration_s": int(s.get("duration", 0)),
                }
                for s in path.get("steps", [])
            ],
        }

    async def geocode(self, address: str, city: str = "") -> dict:
        """地址转经纬度。"""
        params = {"key": self._api_key, "address": address}
        if city:
            params["city"] = city
        resp = await self._http.get(f"{self._base_url}/geocode/geo", params=params)
        data = resp.json()
        if data.get("status") != "1" or not data.get("geocodes"):
            raise AmapClientError(f"地理编码失败: {address}")
        geo = data["geocodes"][0]
        return {
            "location": geo["location"],
            "formatted_address": geo.get("formatted_address", address),
            "level": geo.get("level", ""),
        }

    @staticmethod
    def _normalize_poi(raw: dict) -> dict:
        """将高德原始POI标准化为统一结构。"""
        biz = raw.get("biz_ext", {}) or {}
        return {
            "id": raw.get("id", ""),
            "name": raw.get("name", ""),
            "type": raw.get("type", ""),
            "address": raw.get("address", ""),
            "location": raw.get("location", ""),
            "tel": raw.get("tel", ""),
            "tag": raw.get("tag", ""),
            "rating": biz.get("rating", ""),
            "cost": biz.get("cost", ""),
        }

    async def close(self):
        await self._http.aclose()
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/mcp_servers/test_amap_client.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add mcp_servers/ data/mock/ tests/mcp_servers/test_amap_client.py
git commit -m "feat: add Amap API client with POI search and route planning"
```

---

## Task 3：高德地图 MCP Server

**Files:**
- Create: `mcp_servers/amap/server.py`
- Test: `tests/mcp_servers/test_amap_server.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/mcp_servers/test_amap_server.py`：

```python
"""高德地图 MCP Server 测试。

验证Server正确暴露了工具，且工具调用委托给AmapClient。
"""
import pytest
from unittest.mock import AsyncMock, patch

from mcp_servers.amap.server import create_amap_server


def test_server_exposes_search_attractions_tool():
    """Server 应暴露 search_attractions 工具。"""
    server = create_amap_server(api_key="test-key")
    tools = server._tool_manager._tools
    assert "search_attractions" in tools


def test_server_exposes_get_route_tool():
    """Server 应暴露 get_route 工具。"""
    server = create_amap_server(api_key="test-key")
    tools = server._tool_manager._tools
    assert "get_route" in tools


def test_server_exposes_geocode_tool():
    """Server 应暴露 geocode 工具。"""
    server = create_amap_server(api_key="test-key")
    tools = server._tool_manager._tools
    assert "geocode" in tools


def test_search_attractions_tool_has_correct_schema():
    """search_attractions 工具应有正确的参数schema。"""
    server = create_amap_server(api_key="test-key")
    tool = server._tool_manager._tools["search_attractions"]
    # FastMCP 的 tool 有 name / description / parameters 属性
    assert tool.name == "search_attractions"
    assert "城市" in tool.description or "景点" in tool.description
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/mcp_servers/test_amap_server.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_amap_server'`

- [ ] **Step 3: 实现 MCP Server**

创建 `mcp_servers/amap/server.py`：

```python
"""高德地图 MCP Server。

使用 FastMCP 暴露景点搜索、路线规划和地理编码工具。
设计遵循 Outcome-oriented 原则：工具按业务结果设计，非底层API操作。
"""
from mcp.server.fastmcp import FastMCP

from .client import AmapClient


def create_amap_server(api_key: str) -> FastMCP:
    """创建高德地图 MCP Server 实例。

    Args:
        api_key: 高德 Web 服务 API Key

    Returns:
        FastMCP Server 实例，可通过 stdio 或 HTTP 传输运行
    """
    server = FastMCP("amap-mcp-server")
    client = AmapClient(api_key=api_key)

    @server.tool()
    async def search_attractions(
        city: str, keywords: str = "", category: str = "", limit: int = 20
    ) -> list[dict]:
        """搜索指定城市的旅游景点。

        在规划旅游行程时，用此工具查找目的地有哪些景点可去。
        支持按关键词或类别筛选。

        Args:
            city: 城市名称，如 "杭州"、"北京"
            keywords: 搜索关键词，如 "西湖"、"古镇"
            category: 景点类别，如 "自然风光"、"人文古迹"
            limit: 返回结果数量上限，默认20

        Returns:
            景点列表，每项包含 name/location/address/tel/rating/cost/tag
        """
        return await client.search_attractions(
            city=city, keywords=keywords, category=category, limit=limit
        )

    @server.tool()
    async def get_route(
        origin: str, destination: str, mode: str = "walking"
    ) -> dict:
        """查询两个地点之间的路线规划。

        在安排行程路线时，用此工具计算景点之间的距离和交通时间，
        帮助优化游览顺序、避免走回头路。

        Args:
            origin: 起点经纬度，格式 "经度,纬度"，如 "120.141,30.246"
            destination: 终点经纬度，格式同上
            mode: 交通方式：walking(步行) / driving(驾车) / transit(公交)

        Returns:
            {"distance_m": 距离米, "duration_s": 时长秒, "steps": 导航步骤}
        """
        return await client.get_route(
            origin=origin, destination=destination, mode=mode
        )

    @server.tool()
    async def geocode(address: str, city: str = "") -> dict:
        """将地址文本转换为经纬度坐标。

        当用户提到地名但需要计算路线时，先用此工具获取经纬度。

        Args:
            address: 地址文本，如 "西湖"、"灵隐寺"
            city: 所在城市，提高匹配精度

        Returns:
            {"location": "经度,纬度", "formatted_address": "标准地址", "level": "匹配级别"}
        """
        return await client.geocode(address=address, city=city)

    return server


def main():
    """MCP Server 入口，供 stdio 传输调用。"""
    import os

    api_key = os.environ.get("AMAP_API_KEY", "")
    if not api_key:
        raise RuntimeError("AMAP_API_KEY 环境变量未设置")
    server = create_amap_server(api_key=api_key)
    server.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/mcp_servers/test_amap_server.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 用 MCP Inspector 手动验证**

```bash
# 安装 MCP Inspector（一次性）
npx @modelcontextprotocol/inspector

# 在 Inspector 中配置：
#   Command: python
#   Args: -m mcp_servers.amap.server
#   Env: AMAP_API_KEY=your-real-key
# 验证：tools/list 能看到3个工具，tools/call search_attractions 能返回景点
```

- [ ] **Step 6: Commit**

```bash
git add mcp_servers/amap/server.py tests/mcp_servers/test_amap_server.py
git commit -m "feat: add Amap MCP Server with search_attractions, get_route, geocode tools"
```

---

## Task 4：天气 API 客户端

**Files:**
- Create: `mcp_servers/weather/__init__.py`
- Create: `mcp_servers/weather/client.py`
- Create: `data/mock/weather_now.json`
- Create: `data/mock/weather_forecast.json`
- Test: `tests/mcp_servers/test_weather_client.py`

- [ ] **Step 1: 创建 Mock 数据**

创建 `data/mock/weather_now.json`：

```json
{
  "code": "200",
  "now": {
    "obsTime": "2026-07-01T10:00+08:00",
    "temp": "28",
    "feelsLike": "31",
    "icon": "104",
    "text": "多云",
    "wind360": "180",
    "windDir": "南风",
    "windScale": "2",
    "windSpeed": "8",
    "humidity": "65",
    "precip": "0.0",
    "pressure": "1008",
    "vis": "25",
    "cloud": "45",
    "dew": "22"
  }
}
```

创建 `data/mock/weather_forecast.json`：

```json
{
  "code": "200",
  "daily": [
    {
      "fxDate": "2026-07-01",
      "tempMax": "32",
      "tempMin": "24",
      "iconDay": "104",
      "textDay": "多云",
      "iconNight": "150",
      "textNight": "晴",
      "wind360Day": "180",
      "windDirDay": "南风",
      "windScaleDay": "1-2",
      "humidity": "65",
      "precip": "0.0",
      "uvIndex": "6"
    },
    {
      "fxDate": "2026-07-02",
      "tempMax": "34",
      "tempMin": "25",
      "iconDay": "100",
      "textDay": "晴",
      "iconNight": "150",
      "textNight": "晴",
      "wind360Day": "180",
      "windDirDay": "南风",
      "windScaleDay": "1-2",
      "humidity": "60",
      "precip": "0.0",
      "uvIndex": "8"
    }
  ]
}
```

- [ ] **Step 2: 写失败测试**

创建 `tests/mcp_servers/test_weather_client.py`：

```python
"""和风天气 API 客户端测试。"""
import json
from pathlib import Path

import httpx
import pytest
import respx

from mcp_servers.weather.client import WeatherClient

MOCK_DIR = Path(__file__).parent.parent.parent / "data" / "mock"


@pytest.fixture
def mock_now_response():
    return json.loads((MOCK_DIR / "weather_now.json").read_text())


@pytest.fixture
def mock_forecast_response():
    return json.loads((MOCK_DIR / "weather_forecast.json").read_text())


@pytest.fixture
def client():
    return WeatherClient(api_key="test-weather-key")


@respx.mock
async def test_get_current_weather_returns_normalized(client, mock_now_response):
    """获取实时天气应返回标准化结构。"""
    respx.get("https://api.qweather.com/v7/weather/now").mock(
        return_value=httpx.Response(200, json=mock_now_response)
    )
    result = await client.get_current_weather(location_id="101210101")

    assert result["temp_c"] == 28
    assert result["text"] == "多云"
    assert result["humidity"] == 65
    assert result["wind_dir"] == "南风"


@respx.mock
async def test_get_forecast_returns_daily_list(client, mock_forecast_response):
    """获取预报应返回每日天气列表。"""
    respx.get("https://api.qweather.com/v7/weather/7d").mock(
        return_value=httpx.Response(200, json=mock_forecast_response)
    )
    result = await client.get_forecast(location_id="101210101", days=7)

    assert len(result) == 2
    assert result[0]["date"] == "2026-07-01"
    assert result[0]["temp_max"] == 32
    assert result[0]["temp_min"] == 24


@respx.mock
async def test_get_current_weather_raises_on_error(client):
    """API返回错误码时应抛出异常。"""
    respx.get("https://api.qweather.com/v7/weather/now").mock(
        return_value=httpx.Response(200, json={"code": "401", "refer": []})
    )
    with pytest.raises(Exception, match="和风天气API错误"):
        await client.get_current_weather(location_id="101210101")


@respx.mock
async def test_search_city_by_name(client):
    """城市查询应返回location_id。"""
    respx.get("https://geoapi.qweather.com/v2/city/lookup").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": "200",
                "location": [
                    {"id": "101210101", "name": "杭州", "adm2": "杭州", "lat": "30.246", "lon": "120.141"}
                ],
            },
        )
    )
    result = await client.search_city("杭州")
    assert result["location_id"] == "101210101"
    assert result["name"] == "杭州"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/mcp_servers/test_weather_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'WeatherClient'`

- [ ] **Step 4: 实现 WeatherClient**

创建 `mcp_servers/weather/client.py`：

```python
"""和风天气 API 异步客户端。

和风天气需要先通过城市查询获取 location_id，再查询天气。
本客户端封装这一两步流程，提供按城市名直接查询的便捷接口。
"""
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


class WeatherClientError(Exception):
    """和风天气 API 调用异常。"""


class WeatherClient:
    """和风天气 API 异步客户端。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.qweather.com/v7",
        geo_url: str = "https://geoapi.qweather.com/v2",
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._geo_url = geo_url
        self._http = httpx.AsyncClient(timeout=10.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    async def search_city(self, city_name: str) -> dict:
        """城市名 → location_id。

        Args:
            city_name: 城市名，如 "杭州"

        Returns:
            {"location_id": str, "name": str, "lat": str, "lon": str}
        """
        params = {"key": self._api_key, "location": city_name}
        resp = await self._http.get(f"{self._geo_url}/city/lookup", params=params)
        data = resp.json()

        if data.get("code") != "200" or not data.get("location"):
            raise WeatherClientError(f"城市查询失败: {city_name}")

        loc = data["location"][0]
        return {
            "location_id": loc["id"],
            "name": loc["name"],
            "lat": loc.get("lat", ""),
            "lon": loc.get("lon", ""),
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    async def get_current_weather(self, location_id: str) -> dict:
        """获取实时天气。

        Args:
            location_id: 和风天气城市ID

        Returns:
            标准化天气结构：temp_c/text/humidity/wind_dir/wind_speed/feels_like
        """
        params = {"key": self._api_key, "location": location_id}
        resp = await self._http.get(f"{self._base_url}/weather/now", params=params)
        data = resp.json()

        if data.get("code") != "200":
            raise WeatherClientError(
                f"和风天气API错误: code={data.get('code')}"
            )

        now = data["now"]
        return {
            "obs_time": now.get("obsTime", ""),
            "temp_c": int(now.get("temp", 0)),
            "feels_like": int(now.get("feelsLike", 0)),
            "text": now.get("text", ""),
            "icon": now.get("icon", ""),
            "humidity": int(now.get("humidity", 0)),
            "wind_dir": now.get("windDir", ""),
            "wind_scale": now.get("windScale", ""),
            "wind_speed": now.get("windSpeed", ""),
            "pressure": now.get("pressure", ""),
            "vis": now.get("vis", ""),
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    async def get_forecast(self, location_id: str, days: int = 7) -> list[dict]:
        """获取N日预报。

        Args:
            location_id: 和风天气城市ID
            days: 预报天数（3 或 7）

        Returns:
            每日天气列表
        """
        endpoint = "3d" if days <= 3 else "7d"
        params = {"key": self._api_key, "location": location_id}
        resp = await self._http.get(
            f"{self._base_url}/weather/{endpoint}", params=params
        )
        data = resp.json()

        if data.get("code") != "200":
            raise WeatherClientError(
                f"和风天气API错误: code={data.get('code')}"
            )

        return [
            {
                "date": d.get("fxDate", ""),
                "temp_max": int(d.get("tempMax", 0)),
                "temp_min": int(d.get("tempMin", 0)),
                "text_day": d.get("textDay", ""),
                "text_night": d.get("textNight", ""),
                "wind_dir_day": d.get("windDirDay", ""),
                "wind_scale_day": d.get("windScaleDay", ""),
                "humidity": int(d.get("humidity", 0)),
                "precip": float(d.get("precip", 0)),
                "uv_index": int(d.get("uvIndex", 0)),
            }
            for d in data.get("daily", [])
        ]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    async def get_weather_alert(self, location_id: str) -> list[dict]:
        """获取灾害预警。"""
        params = {"key": self._api_key, "location": location_id}
        resp = await self._http.get(f"{self._base_url}/warning/now", params=params)
        data = resp.json()

        if data.get("code") != "200":
            return []  # 无预警时返回空列表，不视为错误

        return [
            {
                "title": w.get("title", ""),
                "type": w.get("type", ""),
                "level": w.get("level", ""),
                "text": w.get("text", ""),
                "start_time": w.get("startTime", ""),
                "end_time": w.get("endTime", ""),
            }
            for w in data.get("warning", [])
        ]

    async def close(self):
        await self._http.aclose()
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/mcp_servers/test_weather_client.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add mcp_servers/weather/ data/mock/weather_*.json tests/mcp_servers/test_weather_client.py
git commit -m "feat: add QWeather API client with current weather, forecast and alerts"
```

---

## Task 5：天气 MCP Server

**Files:**
- Create: `mcp_servers/weather/server.py`
- Test: `tests/mcp_servers/test_weather_server.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/mcp_servers/test_weather_server.py`：

```python
"""天气 MCP Server 测试。"""
from mcp_servers.weather.server import create_weather_server


def test_server_exposes_get_weather_tool():
    server = create_weather_server(api_key="test-key")
    tools = server._tool_manager._tools
    assert "get_weather" in tools


def test_server_exposes_get_forecast_tool():
    server = create_weather_server(api_key="test-key")
    tools = server._tool_manager._tools
    assert "get_forecast" in tools


def test_server_exposes_get_weather_alert_tool():
    server = create_weather_server(api_key="test-key")
    tools = server._tool_manager._tools
    assert "get_weather_alert" in tools


def test_get_weather_tool_description_mentions_planning():
    """工具描述应包含使用场景说明。"""
    server = create_weather_server(api_key="test-key")
    tool = server._tool_manager._tools["get_weather"]
    assert "天气" in tool.description
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/mcp_servers/test_weather_server.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_weather_server'`

- [ ] **Step 3: 实现 Weather MCP Server**

创建 `mcp_servers/weather/server.py`：

```python
"""天气 MCP Server。

暴露实时天气、天气预报和灾害预警工具。
工具描述中嵌入使用场景说明，帮助LLM理解何时调用。
"""
from mcp.server.fastmcp import FastMCP

from .client import WeatherClient


def create_weather_server(api_key: str) -> FastMCP:
    """创建天气 MCP Server 实例。"""
    server = FastMCP("weather-mcp-server")
    client = WeatherClient(api_key=api_key)

    @server.tool()
    async def get_weather(city: str) -> dict:
        """查询指定城市的实时天气。

        在规划户外活动或行程前，调用此工具检查目的地当前天气状况，
        判断是否适合出行。

        Args:
            city: 城市名称，如 "杭州"、"北京"

        Returns:
            实时天气信息：temp_c(温度) / text(天气状况) / humidity(湿度) /
            wind_dir(风向) / feels_like(体感温度)
        """
        city_info = await client.search_city(city)
        return await client.get_current_weather(location_id=city_info["location_id"])

    @server.tool()
    async def get_forecast(city: str, days: int = 7) -> list[dict]:
        """查询指定城市未来几天的天气预报。

        在规划多日行程时，调用此工具获取每日天气，用于安排室内/室外活动、
        选择出行日期和准备衣物。

        Args:
            city: 城市名称，如 "杭州"
            days: 预报天数，3 或 7，默认7

        Returns:
            每日天气列表：date / temp_max / temp_min / text_day / humidity / precip / uv_index
        """
        city_info = await client.search_city(city)
        return await client.get_forecast(
            location_id=city_info["location_id"], days=days
        )

    @server.tool()
    async def get_weather_alert(city: str) -> list[dict]:
        """查询指定城市的灾害预警信息。

        在出行前或行程中，调用此工具检查是否有暴雨、高温、台风等预警，
        以便及时调整行程安排。

        Args:
            city: 城市名称

        Returns:
            预警列表，每项含 title / type / level / text / start_time / end_time。
            无预警时返回空列表。
        """
        city_info = await client.search_city(city)
        return await client.get_weather_alert(location_id=city_info["location_id"])

    return server


def main():
    """MCP Server 入口。"""
    import os

    api_key = os.environ.get("QWEATHER_API_KEY", "")
    if not api_key:
        raise RuntimeError("QWEATHER_API_KEY 环境变量未设置")
    server = create_weather_server(api_key=api_key)
    server.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/mcp_servers/test_weather_server.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_servers/weather/server.py tests/mcp_servers/test_weather_server.py
git commit -m "feat: add Weather MCP Server with weather, forecast and alert tools"
```

---

## Task 6：景点 MCP Server

**Files:**
- Create: `mcp_servers/scenic/__init__.py`
- Create: `mcp_servers/scenic/client.py`
- Create: `mcp_servers/scenic/server.py`
- Create: `data/mock/scenic_spots.json`
- Test: `tests/mcp_servers/test_scenic_server.py`

景点MCP整合本地景点数据库（门票/开放时间/游览建议）+ 高德POI搜索。MVP阶段先用本地JSON数据，后续迁移到PostgreSQL。

- [ ] **Step 1: 创建景点种子数据**

创建 `data/mock/scenic_spots.json`：

```json
[
  {
    "id": "hangzhou-xihu",
    "name": "西湖",
    "city": "杭州",
    "category": "自然风光",
    "description": "中国著名淡水湖，以"淡妆浓抹总相宜"闻名。环湖有苏堤、白堤、断桥等经典景观。",
    "opening_hours": "全天开放",
    "ticket_price": 0,
    "recommended_duration_hours": 4,
    "best_season": "3-5月, 9-11月",
    "tags": ["世界遗产", "湖泊", "免费"],
    "location": "120.141,30.246",
    "rating": 4.8
  },
  {
    "id": "hangzhou-lingyin",
    "name": "灵隐寺",
    "city": "杭州",
    "category": "人文古迹",
    "description": "江南著名古刹，始建于东晋。寺内飞来峰石刻造像为全国重点文物保护单位。",
    "opening_hours": "07:00-18:15",
    "ticket_price": 30,
    "recommended_duration_hours": 2,
    "best_season": "全年",
    "tags": ["寺庙", "佛教", "历史"],
    "location": "120.087,30.233",
    "rating": 4.7
  },
  {
    "id": "hangzhou-hefang",
    "name": "河坊街",
    "city": "杭州",
    "category": "美食购物",
    "description": "杭州历史文化街区，集中了传统手工艺、特色小吃和老字号店铺。",
    "opening_hours": "全天开放",
    "ticket_price": 0,
    "recommended_duration_hours": 2,
    "best_season": "全年",
    "tags": ["步行街", "小吃", "免费"],
    "location": "120.169,30.249",
    "rating": 4.3
  },
  {
    "id": "beijing-forbidden",
    "name": "故宫博物院",
    "city": "北京",
    "category": "人文古迹",
    "description": "明清两代皇宫，世界现存最大古代宫殿建筑群。藏品涵盖书画、瓷器、钟表等。",
    "opening_hours": "08:30-17:00（周一闭馆）",
    "ticket_price": 60,
    "recommended_duration_hours": 4,
    "best_season": "4-5月, 9-10月",
    "tags": ["世界遗产", "博物馆", "宫殿"],
    "location": "116.397,39.917",
    "rating": 4.9
  }
]
```

- [ ] **Step 2: 写失败测试**

创建 `tests/mcp_servers/test_scenic_server.py`：

```python
"""景点 MCP Server 测试。"""
import json
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch

from mcp_servers.scenic.client import ScenicClient
from mcp_servers.scenic.server import create_scenic_server

MOCK_DIR = Path(__file__).parent.parent.parent / "data" / "mock"


@pytest.fixture
def scenic_client():
    return ScenicClient(data_path=str(MOCK_DIR / "scenic_spots.json"))


def test_server_exposes_search_scenic_spots():
    server = create_scenic_server(data_path=str(MOCK_DIR / "scenic_spots.json"))
    tools = server._tool_manager._tools
    assert "search_scenic_spots" in tools


def test_server_exposes_get_scenic_detail():
    server = create_scenic_server(data_path=str(MOCK_DIR / "scenic_spots.json"))
    tools = server._tool_manager._tools
    assert "get_scenic_detail" in tools


def test_search_scenic_spots_by_city(scenic_client):
    """按城市搜索景点。"""
    result = scenic_client.search_scenic_spots(city="杭州")
    assert len(result) == 3
    assert all(s["city"] == "杭州" for s in result)


def test_search_scenic_spots_by_category(scenic_client):
    """按类别搜索景点。"""
    result = scenic_client.search_scenic_spots(city="杭州", category="自然风光")
    assert len(result) == 1
    assert result[0]["name"] == "西湖"


def test_search_scenic_spots_free_only(scenic_client):
    """筛选免费景点。"""
    result = scenic_client.search_scenic_spots(city="杭州", free_only=True)
    assert all(s["ticket_price"] == 0 for s in result)


def test_get_scenic_detail(scenic_client):
    """获取景点详情。"""
    result = scenic_client.get_scenic_detail("hangzhou-xihu")
    assert result["name"] == "西湖"
    assert result["ticket_price"] == 0
    assert result["recommended_duration_hours"] == 4


def test_get_scenic_detail_not_found(scenic_client):
    """不存在的景点ID返回None。"""
    result = scenic_client.get_scenic_detail("nonexistent")
    assert result is None
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/mcp_servers/test_scenic_server.py -v`
Expected: FAIL — `ImportError: cannot import name 'ScenicClient'`

- [ ] **Step 4: 实现 ScenicClient**

创建 `mcp_servers/scenic/client.py`：

```python
"""景点数据客户端。

MVP阶段从本地JSON文件加载景点数据，后续迁移到PostgreSQL+pgvector。
接口设计保持一致，便于无缝切换数据源。
"""
import json
from pathlib import Path


class ScenicClient:
    """景点数据客户端（本地JSON数据源）。"""

    def __init__(self, data_path: str):
        self._data_path = Path(data_path)
        self._spots: list[dict] = []
        self._load()

    def _load(self):
        """加载景点数据。"""
        if self._data_path.exists():
            self._spots = json.loads(self._data_path.read_text(encoding="utf-8"))

    def search_scenic_spots(
        self,
        city: str = "",
        category: str = "",
        keywords: str = "",
        free_only: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        """搜索景点，支持多条件过滤。

        Args:
            city: 城市名
            category: 类别（自然风光/人文古迹/美食购物）
            keywords: 关键词
            free_only: 仅返回免费景点
            limit: 返回数量上限

        Returns:
            景点列表（不含description长文本，减少token占用）
        """
        results = self._spots

        if city:
            results = [s for s in results if s["city"] == city]
        if category:
            results = [s for s in results if s["category"] == category]
        if free_only:
            results = [s for s in results if s["ticket_price"] == 0]
        if keywords:
            kw = keywords.lower()
            results = [
                s for s in results
                if kw in s["name"].lower()
                or any(kw in t.lower() for t in s.get("tags", []))
            ]

        # 按评分降序
        results = sorted(results, key=lambda x: x.get("rating", 0), reverse=True)
        return [
            {
                "id": s["id"],
                "name": s["name"],
                "city": s["city"],
                "category": s["category"],
                "ticket_price": s["ticket_price"],
                "recommended_duration_hours": s["recommended_duration_hours"],
                "rating": s["rating"],
                "tags": s.get("tags", []),
            }
            for s in results[:limit]
        ]

    def get_scenic_detail(self, spot_id: str) -> dict | None:
        """获取景点完整详情。

        Args:
            spot_id: 景点ID

        Returns:
            完整景点信息，含description/opening_hours/best_season等。
            不存在时返回 None。
        """
        for s in self._spots:
            if s["id"] == spot_id:
                return dict(s)
        return None

    def get_opening_hours(self, spot_id: str) -> str | None:
        """获取景点开放时间。"""
        detail = self.get_scenic_detail(spot_id)
        return detail.get("opening_hours") if detail else None

    def get_ticket_price(self, spot_id: str) -> int | None:
        """获取门票价格。"""
        detail = self.get_scenic_detail(spot_id)
        return detail.get("ticket_price") if detail else None
```

- [ ] **Step 5: 实现 Scenic MCP Server**

创建 `mcp_servers/scenic/server.py`：

```python
"""景点信息 MCP Server。

整合本地景点数据库（门票/开放时间/游览建议），
为规划Agent和推荐Agent提供景点详情查询能力。
"""
from mcp.server.fastmcp import FastMCP

from .client import ScenicClient


def create_scenic_server(data_path: str) -> FastMCP:
    """创建景点 MCP Server 实例。

    Args:
        data_path: 景点数据JSON文件路径
    """
    server = FastMCP("scenic-mcp-server")
    client = ScenicClient(data_path=data_path)

    @server.tool()
    def search_scenic_spots(
        city: str = "",
        category: str = "",
        keywords: str = "",
        free_only: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        """搜索旅游景点。

        在规划行程时，用此工具查找目的地有哪些景点可去。
        支持按城市、类别、关键词筛选，也可筛选免费景点。

        Args:
            city: 城市名，如 "杭州"、"北京"
            category: 类别：自然风光 / 人文古迹 / 美食购物
            keywords: 关键词，如 "寺庙"、"湖泊"
            free_only: 是否只返回免费景点，默认False
            limit: 返回数量上限，默认20

        Returns:
            景点列表，每项含 id/name/category/ticket_price/duration_hours/rating/tags
        """
        return client.search_scenic_spots(
            city=city,
            category=category,
            keywords=keywords,
            free_only=free_only,
            limit=limit,
        )

    @server.tool()
    def get_scenic_detail(spot_id: str) -> dict:
        """获取景点详细信息。

        在确定行程中要去的景点后，用此工具获取开放时间、门票价格、
        建议游览时长等详情，用于行程时间安排和预算计算。

        Args:
            spot_id: 景点ID（从 search_scenic_spots 获取）

        Returns:
            完整景点信息：name/description/opening_hours/ticket_price/
            recommended_duration_hours/best_season/tags/location/rating
        """
        return client.get_scenic_detail(spot_id)

    @server.tool()
    def get_opening_hours(spot_id: str) -> str:
        """查询景点开放时间。

        Args:
            spot_id: 景点ID

        Returns:
            开放时间文本，如 "08:30-17:00（周一闭馆）"
        """
        return client.get_opening_hours(spot_id) or "未知"

    @server.tool()
    def get_ticket_price(spot_id: str) -> int:
        """查询景点门票价格。

        在预算Agent计算行程花费时，用此工具获取景点门票价格。

        Args:
            spot_id: 景点ID

        Returns:
            门票价格（元），0表示免费
        """
        return client.get_ticket_price(spot_id) or 0

    return server


def main():
    """MCP Server 入口。"""
    import os

    data_path = os.environ.get(
        "SCENIC_DATA_PATH", "data/mock/scenic_spots.json"
    )
    server = create_scenic_server(data_path=data_path)
    server.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 运行测试确认通过**

Run: `pytest tests/mcp_servers/test_scenic_server.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Commit**

```bash
git add mcp_servers/scenic/ data/mock/scenic_spots.json tests/mcp_servers/test_scenic_server.py
git commit -m "feat: add Scenic MCP Server with local JSON data source"
```

---

## Task 7：MCP Server 注册配置与集成验证

**Files:**
- Create: `core/mcp_client.py`
- Create: `mcp_servers/registry.py`
- Create: `tests/mcp_servers/test_registry.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/mcp_servers/test_registry.py`：

```python
"""MCP Server 注册配置测试。"""
import json

from mcp_servers.registry import build_mcp_config


def test_build_mcp_config_includes_all_three_servers():
    """注册配置应包含3个MCP Server。"""
    config = build_mcp_config(
        amap_api_key="test-amap",
        qweather_api_key="test-weather",
        scenic_data_path="data/mock/scenic_spots.json",
    )
    assert "amap" in config
    assert "weather" in config
    assert "scenic" in config


def test_amap_config_has_command_and_env():
    """高德Server配置应有command和env。"""
    config = build_mcp_config(
        amap_api_key="test-amap",
        qweather_api_key="test-weather",
        scenic_data_path="data/mock/scenic_spots.json",
    )
    amap = config["amap"]
    assert amap["command"] == "python"
    assert "-m" in amap["args"]
    assert "mcp_servers.amap.server" in amap["args"]
    assert amap["env"]["AMAP_API_KEY"] == "test-amap"


def test_weather_config_has_correct_module():
    """天气Server配置应指向正确模块。"""
    config = build_mcp_config(
        amap_api_key="test-amap",
        qweather_api_key="test-weather",
        scenic_data_path="data/mock/scenic_spots.json",
    )
    weather = config["weather"]
    assert "mcp_servers.weather.server" in weather["args"]
    assert weather["env"]["QWEATHER_API_KEY"] == "test-weather"


def test_scenic_config_has_data_path():
    """景点Server配置应包含数据路径。"""
    config = build_mcp_config(
        amap_api_key="test-amap",
        qweather_api_key="test-weather",
        scenic_data_path="data/mock/scenic_spots.json",
    )
    scenic = config["scenic"]
    assert scenic["env"]["SCENIC_DATA_PATH"] == "data/mock/scenic_spots.json"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/mcp_servers/test_registry.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_mcp_config'`

- [ ] **Step 3: 实现注册配置**

创建 `mcp_servers/registry.py`：

```python
"""MCP Server 注册配置中心。

统一管理3个MCP Server的启动配置，供 MultiServerMCPClient 使用。
配置格式兼容 langchain-mcp-adapters 的 stdio 传输。
"""


def build_mcp_config(
    amap_api_key: str,
    qweather_api_key: str,
    scenic_data_path: str = "data/mock/scenic_spots.json",
) -> dict:
    """构建MCP Server注册配置。

    Args:
        amap_api_key: 高德地图API Key
        qweather_api_key: 和风天气API Key
        scenic_data_path: 景点数据文件路径

    Returns:
        兼容 MultiServerMCPClient 的配置字典：
        {
            "amap": {"command": ..., "args": ..., "env": ...},
            "weather": {...},
            "scenic": {...}
        }
    """
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/mcp_servers/test_registry.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 运行全部MCP Server测试**

Run: `pytest tests/mcp_servers/ -v`
Expected: 全部 PASS (合计约 24 tests)

- [ ] **Step 6: 用 MCP Inspector 验证3个Server**

```bash
# 分别验证3个Server（需填入真实API Key）
# 高德：
AMAP_API_KEY=your-key python -m mcp_servers.amap.server
# 天气：
QWEATHER_API_KEY=your-key python -m mcp_servers.weather.server
# 景点：
SCENIC_DATA_PATH=data/mock/scenic_spots.json python -m mcp_servers.scenic.server

# 或用 Inspector 交互式验证：
npx @modelcontextprotocol/inspector python -m mcp_servers.scenic.server
```

- [ ] **Step 7: Commit**

```bash
git add mcp_servers/registry.py core/mcp_client.py tests/mcp_servers/test_registry.py
git commit -m "feat: add MCP Server registry config for MultiServerMCPClient"
```

---

## Task 8：里程碑 M1 验收

- [ ] **Step 1: 全量测试 + 类型检查 + lint**

Run: `bash scripts/check.sh`
Expected: ruff lint 无违规 / mypy 类型检查无错误 / pytest 全部 PASS，测试通过率 ≥ 90%

- [ ] **Step 2: 检查测试覆盖率**

Run: `pytest tests/ --cov=mcp_servers --cov-report=term-missing`
Expected: mcp_servers 覆盖率 ≥ 80%

- [ ] **Step 3: 验证Docker基础设施**

```bash
docker compose up -d
docker compose ps  # 3个服务应都是 healthy
docker compose down
```

- [ ] **Step 4: 更新总路线图进度**

在 `docs/plans/00-MASTER-ROADMAP.md` 中勾选 Phase 1 完成。

- [ ] **Step 5: 撰写里程碑记录**

在 Obsidian 知识库 `03_项目/tourswarm/日报/` 下创建里程碑记录，
记录：完成的工具、测试覆盖率、遇到的问题、下一步计划。

- [ ] **Step 6: Commit 里程碑**

```bash
git add docs/
git commit -m "milestone: M1 complete — 3 MCP Servers operational with tests"
```

---

## Phase 1 完成标准（M1 验收清单）

- [ ] 3个MCP Server可独立启动，通过stdio通信
- [ ] 高德MCP暴露 search_attractions / get_route / geocode 三个工具
- [ ] 天气MCP暴露 get_weather / get_forecast / get_weather_alert 三个工具
- [ ] 景点MCP暴露 search_scenic_spots / get_scenic_detail / get_opening_hours / get_ticket_price 四个工具
- [ ] 单元测试通过率 ≥ 90%
- [ ] mcp_servers 模块覆盖率 ≥ 80%
- [ ] Docker Compose 可启动 PostgreSQL + Redis + Qdrant
- [ ] MCP Inspector 中可 tools/list + tools/call 验证
- [ ] 所有外部API调用有Mock，开发测试不消耗真实额度
- [ ] `mypy core/ mcp_servers/` 无类型错误
- [ ] `ruff check` 无lint违规
