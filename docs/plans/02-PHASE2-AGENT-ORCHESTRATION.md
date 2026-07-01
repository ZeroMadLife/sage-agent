# Phase 2：Agent 编排核心开发 — TDD 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LangGraph Supervisor + 4个专业Agent跑通端到端协作，CLI脚本可演示"输入一句话→输出结构化行程"。

**Architecture:** Supervisor模式两阶段并行——阶段1信息Agent(天气)+推荐Agent(候选景点)并行；阶段2规划Agent(生成行程)→预算Agent(校验花费，超支则自动降配循环最多3次)。Agent通过MCP工具调用外部数据，生产用MultiServerMCPClient，测试mock client。

**Tech Stack:** Python 3.11+ / LangGraph StateGraph / langgraph-supervisor / langchain-openai / Pydantic v2 / mypy / ruff

**类型安全约定：** 所有函数标注 type hints；行程数据用 Pydantic 模型；每个 Task 完成后跑 mypy + ruff check。

**LLM 分配：**
- Supervisor / 推荐Agent / 信息Agent → DeepSeek（deepseek-chat，快+便宜）
- 规划Agent / 预算Agent → 豆包（Doubao-Seed-2.0-pro，精确推理）

**范围边界：** Phase 2 只做协作链路。记忆系统(Mem0)→Phase 3，确定性验证器→Phase 4。

**预计耗时：** Week 3-4（10个工作日）

---

## 设计决策（grill-me 对齐结果）

| 决策点 | 结论 |
|--------|------|
| MCP集成 | 混合模式：生产 MultiServerMCPClient，测试 mock client |
| ReAct实现 | 全部用框架（create_supervisor + create_react_agent） |
| LLM分配 | 分层：规划/预算用豆包，其余用 DeepSeek |
| 协作方式 | 两阶段并行：信息+推荐 → 规划→预算(冲突反馈) |
| 冲突消解 | 自动降配循环，最大3次迭代 |
| 行程结构 | Pydantic 模型 |
| 验证方式 | CLI 演示脚本 |
| 范围边界 | 不做记忆、不做验证器 |

---

## 文件结构

Phase 2 完成后的目录结构（新增部分标 ★）：

```
tour-agent/
├── core/
│   ├── config/settings.py          # 已有
│   ├── state.py                    # ★ TravelState 定义
│   ├── mcp_client.py               # 已有，扩展为 tool provider
│   └── llm.py                      # ★ LLM 工厂（分层模型分配）
├── agents/
│   ├── __init__.py
│   ├── supervisor.py               # ★ Supervisor 节点
│   ├── planning.py                 # ★ 规划 Agent
│   ├── recommend.py                # ★ 推荐 Agent
│   ├── budget.py                   # ★ 预算 Agent
│   └── info.py                     # ★ 信息 Agent
├── models/
│   ├── __init__.py
│   └── itinerary.py                # ★ Pydantic 行程模型
├── scripts/
│   ├── check.sh                    # 已有
│   └── demo_chat.py                # ★ CLI 演示脚本
├── tests/
│   ├── agents/                     # ★ Agent 测试
│   │   ├── __init__.py
│   │   ├── test_supervisor.py
│   │   ├── test_planning.py
│   │   ├── test_recommend.py
│   │   ├── test_budget.py
│   │   ├── test_info.py
│   │   └── test_e2e.py             # 端到端集成测试
│   └── core/
│       ├── test_config.py          # 已有
│       ├── test_state.py           # ★
│       └── test_llm.py             # ★
└── ...
```

---

## Task 1：行程 Pydantic 模型

**Files:**
- Create: `models/__init__.py`
- Create: `models/itinerary.py`
- Test: `tests/models/__init__.py`
- Test: `tests/models/test_itinerary.py`

- [ ] **Step 1: 创建目录和 __init__.py**

```bash
mkdir -p models tests/models
touch models/__init__.py tests/models/__init__.py
```

- [ ] **Step 2: 写失败测试**

创建 `tests/models/test_itinerary.py`：

```python
"""Itinerary Pydantic 模型测试。"""
import pytest
from models.itinerary import (
    BudgetBreakdown,
    Itinerary,
    ItineraryDay,
    Meal,
    SpotVisit,
    Transport,
)


def test_spot_visit_creation() -> None:
    """景点访问记录可正确创建。"""
    spot = SpotVisit(
        spot_id="hangzhou-xihu",
        name="西湖",
        arrival_time="09:00",
        departure_time="12:00",
        duration_hours=3.0,
        ticket_price=0,
        category="自然风光",
        location="120.141,30.246",
    )
    assert spot.name == "西湖"
    assert spot.ticket_price == 0
    assert spot.duration_hours == 3.0


def test_itinerary_day_creation() -> None:
    """单日行程可正确创建。"""
    day = ItineraryDay(
        date="2026-07-05",
        spots=[
            SpotVisit(
                spot_id="hangzhou-xihu", name="西湖",
                arrival_time="09:00", departure_time="12:00",
                duration_hours=3.0, ticket_price=0,
                category="自然风光", location="120.141,30.246",
            )
        ],
        meals=[Meal(name="午餐", meal_type="lunch", estimated_cost=50)],
        transport=[
            Transport(
                from_name="西湖", to_name="灵隐寺",
                mode="driving", distance_m=7820, duration_s=1234,
            )
        ],
        total_cost=50,
    )
    assert len(day.spots) == 1
    assert day.total_cost == 50


def test_budget_breakdown_creation() -> None:
    """预算分解可正确创建。"""
    budget = BudgetBreakdown(
        total=500,
        spent=480,
        transport=150,
        accommodation=125,
        food=125,
        tickets=75,
        misc=25,
        over_budget=False,
    )
    assert budget.total == 500
    assert budget.over_budget is False


def test_itinerary_full_creation() -> None:
    """完整行程可正确创建。"""
    itinerary = Itinerary(
        destination="杭州",
        days=[],
        total_cost=0,
        weather_summary="多云 24-32度",
        budget=BudgetBreakdown(
            total=500, spent=0, transport=150, accommodation=125,
            food=125, tickets=75, misc=25, over_budget=False,
        ),
    )
    assert itinerary.destination == "杭州"
    assert itinerary.budget.total == 500


def test_itinerary_serializes_to_json() -> None:
    """行程可序列化为 JSON（给前端用）。"""
    itinerary = Itinerary(
        destination="杭州",
        days=[],
        total_cost=0,
        weather_summary="晴",
        budget=BudgetBreakdown(
            total=500, spent=0, transport=150, accommodation=125,
            food=125, tickets=75, misc=25, over_budget=False,
        ),
    )
    data = itinerary.model_dump()
    assert data["destination"] == "杭州"
    assert data["budget"]["total"] == 500
    # 确保 JSON 可序列化
    import json
    json_str = json.dumps(data, ensure_ascii=False)
    assert "杭州" in json_str


def test_budget_over_budget_flag() -> None:
    """花费超过预算时 over_budget 应为 True。"""
    budget = BudgetBreakdown(
        total=500, spent=550, transport=150, accommodation=125,
        food=150, tickets=100, misc=25, over_budget=True,
    )
    assert budget.over_budget is True
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/models/test_itinerary.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: 实现 Pydantic 模型**

创建 `models/itinerary.py`：

```python
"""行程相关的 Pydantic 模型。

这些模型定义了 Agent 输出的数据结构，也是前后端的数据契约。
前端 Vue3/UniApp 将基于这些模型生成 TypeScript 类型。
"""
from pydantic import BaseModel, Field


class SpotVisit(BaseModel):
    """单个景点的访问记录。"""

    spot_id: str = Field(description="景点ID")
    name: str = Field(description="景点名称")
    arrival_time: str = Field(description="到达时间 HH:MM")
    departure_time: str = Field(description="离开时间 HH:MM")
    duration_hours: float = Field(description="建议游览时长（小时）")
    ticket_price: int = Field(default=0, description="门票价格（元）")
    category: str = Field(default="", description="景点类别")
    location: str = Field(default="", description="经纬度 lng,lat")


class Meal(BaseModel):
    """一餐的安排。"""

    name: str = Field(description="餐名，如'午餐·杭帮菜'")
    meal_type: str = Field(description="breakfast/lunch/dinner")
    estimated_cost: int = Field(description="预估花费（元）")


class Transport(BaseModel):
    """景点间的交通安排。"""

    from_name: str = Field(description="出发地名称")
    to_name: str = Field(description="目的地名称")
    mode: str = Field(description="walking/driving/transit")
    distance_m: int = Field(default=0, description="距离（米）")
    duration_s: int = Field(default=0, description="时长（秒）")


class ItineraryDay(BaseModel):
    """单日行程。"""

    date: str = Field(description="日期 YYYY-MM-DD")
    spots: list[SpotVisit] = Field(default_factory=list, description="景点列表")
    meals: list[Meal] = Field(default_factory=list, description="餐饮列表")
    transport: list[Transport] = Field(default_factory=list, description="交通列表")
    total_cost: int = Field(default=0, description="当日总花费（元）")


class BudgetBreakdown(BaseModel):
    """预算分解。"""

    total: int = Field(description="总预算（元）")
    spent: int = Field(description="已分配/已花费（元）")
    transport: int = Field(default=0, description="交通")
    accommodation: int = Field(default=0, description="住宿")
    food: int = Field(default=0, description="餐饮")
    tickets: int = Field(default=0, description="门票")
    misc: int = Field(default=0, description="机动")
    over_budget: bool = Field(default=False, description="是否超支")


class Itinerary(BaseModel):
    """完整行程方案。"""

    destination: str = Field(description="目的地")
    days: list[ItineraryDay] = Field(default_factory=list, description="每日行程")
    total_cost: int = Field(default=0, description="总花费（元）")
    weather_summary: str = Field(default="", description="天气概况")
    budget: BudgetBreakdown | None = Field(default=None, description="预算分解")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/models/test_itinerary.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: 类型检查 + lint**

```bash
mypy models/ tests/models/
ruff check models/ tests/models/
```

- [ ] **Step 7: Commit**

```bash
git add models/ tests/models/
git commit -m "feat: add Pydantic itinerary models (Itinerary/Day/SpotVisit/Budget)"
```

---

## Task 2：TravelState 定义

**Files:**
- Create: `core/state.py`
- Test: `tests/core/test_state.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/core/test_state.py`：

```python
"""TravelState 测试。"""
from langgraph.graph import add_messages

from core.state import TravelState


def test_travel_state_has_required_fields() -> None:
    """TravelState 应包含所有必需字段。"""
    annotations = TravelState.__annotations__
    required = ["messages", "user_id", "session_id", "intent",
                "destination", "budget_total", "dates", "preferences",
                "itinerary", "recommendations", "weather_info",
                "budget_breakdown", "iteration_count", "final_response"]
    for field in required:
        assert field in annotations, f"缺少字段: {field}"


def test_messages_uses_add_messages_reducer() -> None:
    """messages 字段必须使用 add_messages reducer。"""
    # add_messages 是一个函数，不是默认值
    import typing
    hints = typing.get_type_hints(TravelState, include_extras=True)
    messages_hint = hints.get("messages")
    # Annotated[list, add_messages] 的 metadata 应包含 add_messages
    assert hasattr(messages_hint, "__metadata__")
    assert add_messages in messages_hint.__metadata__


def test_travel_state_is_typed_dict() -> None:
    """TravelState 应是 TypedDict。"""
    from typing import TypedDict
    # TypedDict 的子类有 __required_keys__ 和 __optional_keys__
    assert hasattr(TravelState, "__required_keys__")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/core/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 TravelState**

创建 `core/state.py`：

```python
"""LangGraph 全局状态定义。

TravelState 是所有 Agent 共享的状态对象，通过 reducer 合并更新。
每个 Agent 只读写与其职责相关的字段。
"""
from typing import Annotated, Any, TypedDict

from langgraph.graph import add_messages

from models.itinerary import BudgetBreakdown, Itinerary


class TravelState(TypedDict, total=False):
    """多Agent协作的全局状态。

    使用 total=False 表示所有字段都是可选的（初始状态为空）。
    各 Agent 通过返回增量更新来修改状态。
    """

    # ---------- 全局上下文 ----------
    messages: Annotated[list, add_messages]
    user_id: str
    session_id: str

    # ---------- Supervisor 域 ----------
    intent: str  # planning / recommendation / budget / information / combined

    # ---------- 用户需求 ----------
    destination: str
    budget_total: int  # 总预算（元）
    dates: dict[str, str]  # {"start": "2026-07-05", "end": "2026-07-06"}
    preferences: list[str]  # ["美食", "自然风光", "文化"]

    # ---------- Agent 输出 ----------
    itinerary: Itinerary  # 规划Agent输出
    recommendations: list[dict[str, Any]]  # 推荐Agent输出
    weather_info: dict[str, Any]  # 信息Agent输出
    budget_breakdown: BudgetBreakdown  # 预算Agent输出

    # ---------- 编排控制 ----------
    iteration_count: int  # 降配循环计数器
    over_budget: bool  # 预算超支标记
    final_response: str  # 最终回复（终止标记）
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/test_state.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 类型检查 + lint**

```bash
mypy core/state.py tests/core/test_state.py
ruff check core/state.py tests/core/test_state.py
```

- [ ] **Step 6: Commit**

```bash
git add core/state.py tests/core/test_state.py
git commit -m "feat: add TravelState with add_messages reducer and Agent-scoped fields"
```

---

## Task 3：LLM 工厂

**Files:**
- Create: `core/llm.py`
- Test: `tests/core/test_llm.py`

负责根据 `provider:model` 格式创建 LangChain `ChatOpenAI` 实例（所有 provider 都兼容 OpenAI 格式）。

- [ ] **Step 1: 写失败测试**

创建 `tests/core/test_llm.py`：

```python
"""LLM 工厂测试。"""
from unittest.mock import patch

import pytest

from core.llm import create_llm


def test_create_llm_doubao() -> None:
    """create_llm 创建豆包模型。"""
    with patch.dict("os.environ", {
        "DOUBAO_API_KEY": "test-doubao",
        "DOUBAO_BASE_URL": "https://ark.cn-beijing.volces.com/api/coding/v3",
    }):
        llm = create_llm("doubao:Doubao-Seed-2.0-pro")
        assert llm is not None
        assert llm.model == "Doubao-Seed-2.0-pro"


def test_create_llm_deepseek() -> None:
    """create_llm 创建 DeepSeek 模型。"""
    with patch.dict("os.environ", {
        "DEEPSEEK_API_KEY": "test-ds",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
    }):
        llm = create_llm("deepseek:deepseek-chat")
        assert llm is not None
        assert llm.model == "deepseek-chat"


def test_create_llm_unknown_provider_raises() -> None:
    """未知 provider 抛出 ValueError。"""
    with pytest.raises(ValueError, match="未知 LLM provider"):
        create_llm("unknown:model")


def test_create_llm_missing_key_raises() -> None:
    """provider 的 key 未配置时抛出 ValueError。"""
    # openai key 默认为空
    with pytest.raises(ValueError, match="API key"):
        create_llm("openai:gpt-4o")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/core/test_llm.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 LLM 工厂**

创建 `core/llm.py`：

```python
"""LLM 工厂 — 根据 provider:model 创建 LangChain ChatOpenAI 实例。

所有 provider（豆包/DeepSeek/180txt/OpenAI）都兼容 OpenAI API 格式，
统一用 ChatOpenAI 创建，只需切换 api_key / base_url / model。

模型分配策略：
  - 规划Agent / 预算Agent → doubao:Doubao-Seed-2.0-pro（精确推理）
  - Supervisor / 推荐Agent / 信息Agent → deepseek:deepseek-chat（快+便宜）
"""
import os

from langchain_openai import ChatOpenAI

# provider → (env_var_name, default_base_url, default_model)
_PROVIDER_MAP: dict[str, tuple[str, str, str]] = {
    "doubao": ("DOUBAO_API_KEY", "https://ark.cn-beijing.volces.com/api/coding/v3", "Doubao-Seed-2.0-pro"),
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", "deepseek-chat"),
    "openai_proxy": ("OPENAI_PROXY_API_KEY", "https://serve.wzjself.org/v1", "gpt-5.4-mini"),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o"),
}


def create_llm(model_spec: str, temperature: float = 0.0, **kwargs: object) -> ChatOpenAI:
    """根据 'provider:model' 创建 ChatOpenAI 实例。

    Args:
        model_spec: 'provider:model' 格式，如 'doubao:Doubao-Seed-2.0-pro'
        temperature: 温度参数，默认0（确定性输出，适合编排）
        **kwargs: 传递给 ChatOpenAI 的额外参数

    Returns:
        ChatOpenAI 实例

    Raises:
        ValueError: 未知 provider 或 API key 未配置
    """
    provider, _, model = model_spec.partition(":")

    if provider not in _PROVIDER_MAP:
        raise ValueError(f"未知 LLM provider: {provider}（支持: {list(_PROVIDER_MAP)}）")

    env_var, default_base_url, default_model = _PROVIDER_MAP[provider]
    api_key = os.environ.get(env_var, "")
    if not api_key:
        raise ValueError(f"LLM provider '{provider}' 的 API key 未配置（环境变量: {env_var}）")

    base_url = os.environ.get(f"{provider.upper()}_BASE_URL", default_base_url)
    resolved_model = model.strip() or default_model

    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=resolved_model,
        temperature=temperature,
        **kwargs,  # type: ignore[arg-type]
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/test_llm.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 类型检查 + lint**

```bash
mypy core/llm.py tests/core/test_llm.py
ruff check core/llm.py tests/core/test_llm.py
```

- [ ] **Step 6: Commit**

```bash
git add core/llm.py tests/core/test_llm.py
git commit -m "feat: add LLM factory with provider:model spec and tiered model assignment"
```

---

## Task 4：信息 Agent

**Files:**
- Create: `agents/info.py`
- Test: `tests/agents/__init__.py`
- Test: `tests/agents/test_info.py`

信息Agent负责并行查询天气和景点信息。第一阶段执行，结果供规划Agent使用。

- [ ] **Step 1: 创建 __init__.py**

```bash
touch tests/agents/__init__.py
```

- [ ] **Step 2: 写失败测试**

创建 `tests/agents/test_info.py`：

```python
"""信息 Agent 测试。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.info import create_info_agent, info_node


@pytest.fixture
def mock_weather_client() -> MagicMock:
    client = MagicMock()
    client.search_city = AsyncMock(return_value={"location_id": "101210101", "name": "杭州"})
    client.get_current_weather = AsyncMock(return_value={
        "temp_c": 28, "text": "多云", "humidity": 65, "wind_dir": "南风",
    })
    client.get_forecast = AsyncMock(return_value=[
        {"date": "2026-07-05", "temp_max": 32, "temp_min": 24, "text_day": "多云"},
        {"date": "2026-07-06", "temp_max": 34, "temp_min": 25, "text_day": "晴"},
    ])
    return client


@pytest.fixture
def mock_scenic_client() -> MagicMock:
    client = MagicMock()
    client.search_scenic_spots = MagicMock(return_value=[
        {"id": "hangzhou-xihu", "name": "西湖", "ticket_price": 0, "rating": 4.8},
        {"id": "hangzhou-lingyin", "name": "灵隐寺", "ticket_price": 30, "rating": 4.7},
    ])
    return client


async def test_info_node_returns_weather_and_spots(
    mock_weather_client: MagicMock, mock_scenic_client: MagicMock
) -> None:
    """info_node 应返回天气信息和候选景点。"""
    state = {
        "destination": "杭州",
        "dates": {"start": "2026-07-05", "end": "2026-07-06"},
        "preferences": ["自然风光"],
        "messages": [],
    }
    result = await info_node(state, mock_weather_client, mock_scenic_client)

    assert "weather_info" in result
    assert result["weather_info"]["current"]["temp_c"] == 28
    assert "forecast" in result["weather_info"]
    assert len(result["weather_info"]["forecast"]) == 2
    assert "recommendations" in result
    assert len(result["recommendations"]) == 2


async def test_info_node_handles_weather_error(
    mock_scenic_client: MagicMock
) -> None:
    """天气查询失败时仍返回景点数据（优雅降级）。"""
    weather_client = MagicMock()
    weather_client.search_city = AsyncMock(side_effect=Exception("API error"))

    state = {
        "destination": "杭州",
        "dates": {"start": "2026-07-05", "end": "2026-07-06"},
        "preferences": [],
        "messages": [],
    }
    result = await info_node(state, weather_client, mock_scenic_client)

    # 天气失败时 weather_info 应有降级标记
    assert result["weather_info"]["error"] is True
    # 景点数据仍应正常返回
    assert len(result["recommendations"]) == 2


async def test_info_node_searches_by_preferences(
    mock_weather_client: MagicMock, mock_scenic_client: MagicMock
) -> None:
    """应根据用户偏好搜索景点。"""
    state = {
        "destination": "杭州",
        "dates": {"start": "2026-07-05", "end": "2026-07-06"},
        "preferences": ["美食"],
        "messages": [],
    }
    await info_node(state, mock_weather_client, mock_scenic_client)

    mock_scenic_client.search_scenic_spots.assert_called_once()
    call_kwargs = mock_scenic_client.search_scenic_spots.call_args
    assert call_kwargs.kwargs.get("city") == "杭州"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/agents/test_info.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: 实现信息 Agent**

创建 `agents/info.py`：

```python
"""信息 Agent — 天气/景点信息聚合。

第一阶段执行：并行查询天气和候选景点，结果供规划Agent使用。
天气查询失败时优雅降级，不影响行程规划核心流程。
"""
import logging
from typing import Any

from mcp_servers.amap.client import AmapClient
from mcp_servers.scenic.client import ScenicClient
from mcp_servers.weather.client import WeatherClient

logger = logging.getLogger(__name__)


async def info_node(
    state: dict[str, Any],
    weather_client: WeatherClient,
    scenic_client: ScenicClient,
) -> dict[str, Any]:
    """信息聚合节点：查询天气 + 搜索候选景点。

    Args:
        state: 当前 TravelState
        weather_client: 和风天气客户端
        scenic_client: 景点数据客户端

    Returns:
        状态增量：{"weather_info": ..., "recommendations": ...}
    """
    destination = state.get("destination", "")
    preferences = state.get("preferences", [])

    # --- 查询天气 ---
    weather_info: dict[str, Any] = {}
    try:
        city_info = await weather_client.search_city(destination)
        location_id = city_info["location_id"]
        current = await weather_client.get_current_weather(location_id)
        forecast = await weather_client.get_forecast(location_id, days=7)
        weather_info = {
            "current": current,
            "forecast": forecast,
            "error": False,
        }
    except Exception as e:
        logger.warning("天气查询失败: %s", e)
        weather_info = {
            "current": None,
            "forecast": [],
            "error": True,
            "message": f"天气信息暂不可用: {e}",
        }

    # --- 搜索候选景点 ---
    # 根据偏好构造搜索关键词
    keywords = " ".join(preferences) if preferences else ""
    spots = scenic_client.search_scenic_spots(
        city=destination,
        keywords=keywords,
        limit=20,
    )

    return {
        "weather_info": weather_info,
        "recommendations": spots,
    }


def create_info_agent(
    weather_client: WeatherClient,
    scenic_client: ScenicClient,
) -> Any:
    """创建信息 Agent 的可调用节点。

    返回一个 async 函数，签名符合 LangGraph 节点要求：(state) -> dict
    """
    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        return await info_node(state, weather_client, scenic_client)

    return _node
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/agents/test_info.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: 类型检查 + lint**

```bash
mypy agents/info.py tests/agents/test_info.py
ruff check agents/info.py tests/agents/test_info.py
```

- [ ] **Step 7: Commit**

```bash
git add agents/info.py tests/agents/__init__.py tests/agents/test_info.py
git commit -m "feat: add Info Agent with weather query and scenic spot search"
```

---

## Task 5：规划 Agent

**Files:**
- Create: `agents/planning.py`
- Test: `tests/agents/test_planning.py`

规划Agent是核心——根据天气、候选景点、预算、偏好生成多日行程。使用豆包模型做复杂推理。

- [ ] **Step 1: 写失败测试**

创建 `tests/agents/test_planning.py`：

```python
"""规划 Agent 测试。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.planning import create_planning_prompt, planning_node


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(
        content='{"destination": "杭州", "days": [{"date": "2026-07-05", "spots": [{"spot_id": "hangzhou-xihu", "name": "西湖", "arrival_time": "09:00", "departure_time": "12:00", "duration_hours": 3.0, "ticket_price": 0, "category": "自然风光", "location": "120.141,30.246"}], "meals": [{"name": "午餐", "meal_type": "lunch", "estimated_cost": 50}], "transport": [], "total_cost": 50}], "total_cost": 50, "weather_summary": "多云 24-32度", "budget": null}'
    ))
    return llm


async def test_planning_node_returns_itinerary(mock_llm: MagicMock) -> None:
    """planning_node 应返回解析后的 Itinerary 对象。"""
    state = {
        "destination": "杭州",
        "dates": {"start": "2026-07-05", "end": "2026-07-06"},
        "budget_total": 500,
        "preferences": ["自然风光", "美食"],
        "weather_info": {"current": {"temp_c": 28, "text": "多云"}, "error": False},
        "recommendations": [{"id": "hangzhou-xihu", "name": "西湖", "ticket_price": 0}],
        "messages": [],
    }
    result = await planning_node(state, mock_llm)

    assert "itinerary" in result
    assert result["itinerary"].destination == "杭州"
    assert len(result["itinerary"].days) == 1
    assert result["itinerary"].days[0].spots[0].name == "西湖"


async def test_planning_node_handles_llm_error() -> None:
    """LLM 返回无效 JSON 时应抛出有意义的异常。"""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="这不是JSON"))

    state = {
        "destination": "杭州",
        "dates": {"start": "2026-07-05", "end": "2026-07-06"},
        "budget_total": 500,
        "preferences": [],
        "weather_info": {"error": False},
        "recommendations": [],
        "messages": [],
    }
    with pytest.raises(Exception, match="JSON"):
        await planning_node(state, llm)


def test_create_planning_prompt_includes_all_context() -> None:
    """prompt 应包含目的地、日期、预算、偏好、天气、候选景点。"""
    prompt = create_planning_prompt(
        destination="杭州",
        dates={"start": "2026-07-05", "end": "2026-07-06"},
        budget_total=500,
        preferences=["美食", "自然风光"],
        weather_info={"current": {"temp_c": 28, "text": "多云"}, "error": False},
        recommendations=[{"id": "x", "name": "西湖", "ticket_price": 0, "rating": 4.8}],
    )
    assert "杭州" in prompt
    assert "500" in prompt
    assert "美食" in prompt
    assert "多云" in prompt
    assert "西湖" in prompt
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/agents/test_planning.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现规划 Agent**

创建 `agents/planning.py`：

```python
"""规划 Agent — 行程生成核心。

根据天气、候选景点、预算、偏好生成多日行程方案。
使用豆包模型（Doubao-Seed-2.0-pro）做复杂推理。

执行流程：
1. 构造包含所有上下文的 prompt（天气/景点/预算/偏好）
2. 调用 LLM 生成行程 JSON
3. 解析为 Pydantic Itinerary 模型
"""
import json
import logging
from typing import Any

from langchain_openai import ChatOpenAI
from models.itinerary import Itinerary

logger = logging.getLogger(__name__)

PLANNING_SYSTEM_PROMPT = """你是一个专业的旅游行程规划师。根据用户的需求和约束条件，生成详细的多日旅游行程。

输出要求：
1. 必须输出严格的 JSON 格式，符合以下结构：
{
  "destination": "目的地",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "spots": [
        {
          "spot_id": "景点ID",
          "name": "景点名称",
          "arrival_time": "HH:MM",
          "departure_time": "HH:MM",
          "duration_hours": 小时数,
          "ticket_price": 门票价格,
          "category": "类别",
          "location": "经度,纬度"
        }
      ],
      "meals": [
        {"name": "餐名", "meal_type": "breakfast/lunch/dinner", "estimated_cost": 花费}
      ],
      "transport": [
        {"from_name": "出发地", "to_name": "目的地", "mode": "walking/driving/transit", "distance_m": 距离, "duration_s": 时长秒}
      ],
      "total_cost": 当日总花费
    }
  ],
  "total_cost": 总花费,
  "weather_summary": "天气概况",
  "budget": null
}

规划原则：
- 每天3-5个景点，避免过于紧凑
- 考虑景点间距离和交通时间，少走回头路
- 预留用餐和休息时间
- 根据天气调整室内/室外活动安排
- 门票价格和餐饮花费要合理估算
- total_cost 是所有 days 的花费总和
"""


def create_planning_prompt(
    destination: str,
    dates: dict[str, str],
    budget_total: int,
    preferences: list[str],
    weather_info: dict[str, Any],
    recommendations: list[dict[str, Any]],
) -> str:
    """构造规划 prompt，包含所有上下文信息。"""
    weather_desc = "天气信息不可用"
    if not weather_info.get("error"):
        current = weather_info.get("current", {})
        forecast = weather_info.get("forecast", [])
        weather_desc = f"当前: {current.get('text', '未知')} {current.get('temp_c', '?')}°C"
        if forecast:
            weather_desc += f"\n预报: {'; '.join(f\"{f.get('date','')}: {f.get('text_day','')} {f.get('temp_min','')}-{f.get('temp_max','')}°C\" for f in forecast)}"

    spots_desc = "\n".join(
        f"- {s.get('name', '')} (ID: {s.get('id', '')}, 门票: {s.get('ticket_price', 0)}元, 评分: {s.get('rating', 0)})"
        for s in recommendations
    ) or "无候选景点"

    prefs_desc = "、".join(preferences) if preferences else "无特殊偏好"

    return f"""请为以下需求生成旅游行程：

目的地: {destination}
日期: {dates.get('start', '')} 至 {dates.get('end', '')}
预算: {budget_total}元
偏好: {prefs_desc}

天气信息:
{weather_desc}

候选景点:
{spots_desc}

请生成行程方案，确保总花费不超过{budget_total}元。只输出JSON，不要其他文字。"""


async def planning_node(state: dict[str, Any], llm: ChatOpenAI) -> dict[str, Any]:
    """规划节点：调用 LLM 生成行程。

    Args:
        state: 当前 TravelState
        llm: ChatOpenAI 实例（豆包模型）

    Returns:
        状态增量：{"itinerary": Itinerary}
    """
    prompt = create_planning_prompt(
        destination=state.get("destination", ""),
        dates=state.get("dates", {}),
        budget_total=state.get("budget_total", 0),
        preferences=state.get("preferences", []),
        weather_info=state.get("weather_info", {}),
        recommendations=state.get("recommendations", []),
    )

    messages = [
        {"role": "system", "content": PLANNING_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    response = await llm.ainvoke(messages)
    content = response.content if hasattr(response, "content") else str(response)

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.error("LLM 返回无效 JSON: %s", content[:200])
        raise ValueError(f"LLM 返回的内容不是有效 JSON: {e}") from e

    itinerary = Itinerary.model_validate(data)
    return {"itinerary": itinerary}


def create_planning_agent(llm: ChatOpenAI) -> Any:
    """创建规划 Agent 的可调用节点。"""
    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        return await planning_node(state, llm)

    return _node
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/agents/test_planning.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 类型检查 + lint**

```bash
mypy agents/planning.py tests/agents/test_planning.py
ruff check agents/planning.py tests/agents/test_planning.py
```

- [ ] **Step 6: Commit**

```bash
git add agents/planning.py tests/agents/test_planning.py
git commit -m "feat: add Planning Agent with LLM-powered itinerary generation"
```

---

## Task 6：预算 Agent

**Files:**
- Create: `agents/budget.py`
- Test: `tests/agents/test_budget.py`

预算Agent校验行程花费是否超支，超支则返回降配建议。

- [ ] **Step 1: 写失败测试**

创建 `tests/agents/test_budget.py`：

```python
"""预算 Agent 测试。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.budget import budget_node, calculate_budget_breakdown
from models.itinerary import BudgetBreakdown, Itinerary, ItineraryDay, SpotVisit


def _make_itinerary(total_cost: int = 300) -> Itinerary:
    """构造测试用行程。"""
    return Itinerary(
        destination="杭州",
        days=[
            ItineraryDay(
                date="2026-07-05",
                spots=[SpotVisit(
                    spot_id="x", name="西湖", arrival_time="09:00",
                    departure_time="12:00", duration_hours=3.0,
                    ticket_price=0, category="自然", location="120,30",
                )],
                meals=[],
                transport=[],
                total_cost=total_cost,
            )
        ],
        total_cost=total_cost,
        weather_summary="晴",
    )


def test_calculate_budget_within_budget() -> None:
    """花费在预算内时 over_budget 为 False。"""
    breakdown = calculate_budget_breakdown(
        itinerary=_make_itinerary(total_cost=300),
        budget_total=500,
        transport=150,
        accommodation=125,
        food=125,
        tickets=75,
        misc=25,
    )
    assert breakdown.over_budget is False
    assert breakdown.spent == 300


def test_calculate_budget_over_budget() -> None:
    """花费超过预算时 over_budget 为 True。"""
    breakdown = calculate_budget_breakdown(
        itinerary=_make_itinerary(total_cost=600),
        budget_total=500,
        transport=150,
        accommodation=125,
        food=200,
        tickets=100,
        misc=25,
    )
    assert breakdown.over_budget is True
    assert breakdown.spent == 600


async def test_budget_node_returns_breakdown() -> None:
    """budget_node 应返回预算分解。"""
    state = {
        "itinerary": _make_itinerary(total_cost=300),
        "budget_total": 500,
        "messages": [],
    }
    mock_llm = MagicMock()
    result = await budget_node(state, mock_llm)

    assert "budget_breakdown" in result
    assert result["budget_breakdown"].total == 500
    assert result["budget_breakdown"].over_budget is False
    assert "over_budget" in result


async def test_budget_node_flags_over_budget() -> None:
    """超支时应设置 over_budget=True。"""
    state = {
        "itinerary": _make_itinerary(total_cost=600),
        "budget_total": 500,
        "messages": [],
    }
    mock_llm = MagicMock()
    result = await budget_node(state, mock_llm)

    assert result["over_budget"] is True


async def test_budget_node_increments_iteration() -> None:
    """每次调用应递增 iteration_count。"""
    state = {
        "itinerary": _make_itinerary(total_cost=600),
        "budget_total": 500,
        "iteration_count": 1,
        "messages": [],
    }
    mock_llm = MagicMock()
    result = await budget_node(state, mock_llm)

    assert result["iteration_count"] == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/agents/test_budget.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现预算 Agent**

创建 `agents/budget.py`：

```python
"""预算 Agent — 预算分配、花费校验、超支检测。

规划Agent生成行程后，预算Agent校验总花费是否在预算内。
超支时设置 over_budget=True，触发 Supervisor 的降配循环。

预算分配比例（默认）:
  交通 30% / 住宿 25% / 餐饮 25% / 门票 15% / 机动 5%
"""
import logging
from typing import Any

from langchain_openai import ChatOpenAI
from models.itinerary import BudgetBreakdown, Itinerary

logger = logging.getLogger(__name__)

# 默认预算分配比例
DEFAULT_BUDGET_RATIOS = {
    "transport": 0.30,
    "accommodation": 0.25,
    "food": 0.25,
    "tickets": 0.15,
    "misc": 0.05,
}


def calculate_budget_breakdown(
    itinerary: Itinerary,
    budget_total: int,
    transport: int = 0,
    accommodation: int = 0,
    food: int = 0,
    tickets: int = 0,
    misc: int = 0,
) -> BudgetBreakdown:
    """计算预算分解。

    Args:
        itinerary: 行程方案
        budget_total: 总预算
        transport/accommodation/food/tickets/misc: 各类别已分配金额

    Returns:
        BudgetBreakdown 含 over_budget 标记
    """
    spent = itinerary.total_cost
    over_budget = spent > budget_total

    return BudgetBreakdown(
        total=budget_total,
        spent=spent,
        transport=transport,
        accommodation=accommodation,
        food=food,
        tickets=tickets,
        misc=misc,
        over_budget=over_budget,
    )


async def budget_node(state: dict[str, Any], llm: ChatOpenAI) -> dict[str, Any]:
    """预算校验节点。

    Args:
        state: 当前 TravelState（需包含 itinerary 和 budget_total）
        llm: ChatOpenAI 实例（豆包模型，用于超支时的降配建议）

    Returns:
        状态增量：{"budget_breakdown": ..., "over_budget": ..., "iteration_count": ...}
    """
    itinerary: Itinerary = state["itinerary"]
    budget_total: int = state.get("budget_total", 0)
    iteration_count: int = state.get("iteration_count", 0)

    # 按默认比例分配预算（MVP阶段简化处理）
    breakdown = calculate_budget_breakdown(
        itinerary=itinerary,
        budget_total=budget_total,
        transport=int(budget_total * DEFAULT_BUDGET_RATIOS["transport"]),
        accommodation=int(budget_total * DEFAULT_BUDGET_RATIOS["accommodation"]),
        food=int(budget_total * DEFAULT_BUDGET_RATIOS["food"]),
        tickets=int(budget_total * DEFAULT_BUDGET_RATIOS["tickets"]),
        misc=int(budget_total * DEFAULT_BUDGET_RATIOS["misc"]),
    )

    if breakdown.over_budget:
        logger.warning(
            "预算超支: 花费 %d > 预算 %d (迭代 %d)",
            breakdown.spent, budget_total, iteration_count + 1,
        )

    return {
        "budget_breakdown": breakdown,
        "over_budget": breakdown.over_budget,
        "iteration_count": iteration_count + 1,
    }


def create_budget_agent(llm: ChatOpenAI) -> Any:
    """创建预算 Agent 的可调用节点。"""
    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        return await budget_node(state, llm)

    return _node
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/agents/test_budget.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 类型检查 + lint**

```bash
mypy agents/budget.py tests/agents/test_budget.py
ruff check agents/budget.py tests/agents/test_budget.py
```

- [ ] **Step 6: Commit**

```bash
git add agents/budget.py tests/agents/test_budget.py
git commit -m "feat: add Budget Agent with breakdown calculation and over-budget detection"
```

---

## Task 7：推荐 Agent

**Files:**
- Create: `agents/recommend.py`
- Test: `tests/agents/test_recommend.py`

推荐Agent在阶段1与信息Agent并行，基于偏好从景点数据库检索候选。MVP阶段直接复用信息Agent的景点搜索结果做排序，Phase 3加向量检索。

- [ ] **Step 1: 写失败测试**

创建 `tests/agents/test_recommend.py`：

```python
"""推荐 Agent 测试。"""
from unittest.mock import MagicMock

import pytest

from agents.recommend import recommend_node


@pytest.fixture
def mock_scenic_client() -> MagicMock:
    client = MagicMock()
    client.search_scenic_spots = MagicMock(return_value=[
        {"id": "1", "name": "西湖", "ticket_price": 0, "rating": 4.8, "category": "自然风光"},
        {"id": "2", "name": "灵隐寺", "ticket_price": 30, "rating": 4.7, "category": "人文古迹"},
        {"id": "3", "name": "河坊街", "ticket_price": 0, "rating": 4.3, "category": "美食购物"},
    ])
    return client


def test_recommend_node_returns_ranked_spots(mock_scenic_client: MagicMock) -> None:
    """推荐节点应返回按评分排序的景点。"""
    state = {
        "destination": "杭州",
        "preferences": ["自然风光"],
        "budget_total": 500,
        "messages": [],
    }
    result = recommend_node(state, mock_scenic_client)

    assert "recommendations" in result
    spots = result["recommendations"]
    assert len(spots) == 3
    # 按评分降序
    assert spots[0]["rating"] >= spots[1]["rating"]


def test_recommend_node_filters_by_budget(mock_scenic_client: MagicMock) -> None:
    """低预算时优先推荐免费景点。"""
    state = {
        "destination": "杭州",
        "preferences": [],
        "budget_total": 100,  # 极低预算
        "messages": [],
    }
    result = recommend_node(state, mock_scenic_client)

    spots = result["recommendations"]
    # 免费景点应排在前面
    free_spots = [s for s in spots if s["ticket_price"] == 0]
    assert len(free_spots) >= 1


def test_recommend_node_handles_empty_preferences(mock_scenic_client: MagicMock) -> None:
    """无偏好时返回全部景点。"""
    state = {
        "destination": "杭州",
        "preferences": [],
        "budget_total": 500,
        "messages": [],
    }
    result = recommend_node(state, mock_scenic_client)

    assert len(result["recommendations"]) == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/agents/test_recommend.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现推荐 Agent**

创建 `agents/recommend.py`：

```python
"""推荐 Agent — 景点个性化推荐。

阶段1与信息Agent并行执行，基于用户偏好和预算检索候选景点。
MVP阶段用简单排序（评分+免费优先），Phase 3加向量检索+混合推荐。
"""
from typing import Any

from mcp_servers.scenic.client import ScenicClient


def recommend_node(
    state: dict[str, Any],
    scenic_client: ScenicClient,
) -> dict[str, Any]:
    """推荐节点：基于偏好和预算搜索并排序景点。

    Args:
        state: 当前 TravelState
        scenic_client: 景点数据客户端

    Returns:
        状态增量：{"recommendations": [...]}
    """
    destination = state.get("destination", "")
    preferences = state.get("preferences", [])
    budget_total = state.get("budget_total", 0)

    keywords = " ".join(preferences) if preferences else ""

    spots = scenic_client.search_scenic_spots(
        city=destination,
        keywords=keywords,
        limit=20,
    )

    # 低预算时优先免费景点
    if budget_total < 200:
        spots.sort(
            key=lambda s: (s.get("ticket_price", 0), -s.get("rating", 0))
        )
    else:
        spots.sort(key=lambda s: s.get("rating", 0), reverse=True)

    return {"recommendations": spots}


def create_recommend_agent(scenic_client: ScenicClient) -> Any:
    """创建推荐 Agent 的可调用节点。"""
    def _node(state: dict[str, Any]) -> dict[str, Any]:
        return recommend_node(state, scenic_client)

    return _node
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/agents/test_recommend.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 类型检查 + lint**

```bash
mypy agents/recommend.py tests/agents/test_recommend.py
ruff check agents/recommend.py tests/agents/test_recommend.py
```

- [ ] **Step 6: Commit**

```bash
git add agents/recommend.py tests/agents/test_recommend.py
git commit -m "feat: add Recommend Agent with budget-aware spot ranking"
```

---

## Task 8：Supervisor + StateGraph 编排

**Files:**
- Create: `agents/supervisor.py`
- Create: `agents/graph.py`
- Test: `tests/agents/test_supervisor.py`

Supervisor是编排中枢——两阶段并行 + 预算降配循环。这是整个Phase 2最复杂的部分。

- [ ] **Step 1: 写失败测试**

创建 `tests/agents/test_supervisor.py`：

```python
"""Supervisor 编排测试。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.graph import build_graph, should_replan


def test_should_replan_returns_planning_when_over_budget() -> None:
    """超支且未达迭代上限时应返回 planning。"""
    state = {"over_budget": True, "iteration_count": 1}
    assert should_replan(state) == "planning"


def test_should_replan_returns_end_when_iteration_exceeded() -> None:
    """迭代超过3次时应返回 END。"""
    from langgraph.graph import END
    state = {"over_budget": True, "iteration_count": 3}
    assert should_replan(state) == END


def test_should_replan_returns_end_when_within_budget() -> None:
    """预算内时应返回 END。"""
    from langgraph.graph import END
    state = {"over_budget": False, "iteration_count": 1}
    assert should_replan(state) == END


def test_build_graph_returns_compiled_graph() -> None:
    """build_graph 应返回可编译的图。"""
    mock_weather = MagicMock()
    mock_scenic = MagicMock()
    mock_planning_llm = MagicMock()
    mock_budget_llm = MagicMock()

    graph = build_graph(
        weather_client=mock_weather,
        scenic_client=mock_scenic,
        planning_llm=mock_planning_llm,
        budget_llm=mock_budget_llm,
    )
    assert graph is not None
    # 编译后的图应有 invoke 方法
    assert hasattr(graph, "ainvoke")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/agents/test_supervisor.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 Supervisor 和 Graph**

创建 `agents/supervisor.py`：

```python
"""Supervisor — 意图识别 + 任务分解 + 两阶段调度。

两阶段并行架构：
  阶段1: 信息Agent(天气) + 推荐Agent(候选景点) 并行
  阶段2: 规划Agent(生成行程) → 预算Agent(校验花费)
         超支则反馈回规划Agent（最大3次迭代）
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 3


def should_replan(state: dict[str, Any]) -> str:
    """预算校验后的路由函数：决定是否重新规划。

    Args:
        state: 当前状态

    Returns:
        "planning"（重新规划）或 END（结束）
    """
    from langgraph.graph import END

    over_budget = state.get("over_budget", False)
    iteration_count = state.get("iteration_count", 0)

    if over_budget and iteration_count < MAX_ITERATIONS:
        logger.info("超支，触发降配重规划 (迭代 %d/%d)", iteration_count, MAX_ITERATIONS)
        return "planning"

    if over_budget:
        logger.warning("降配迭代已达上限 %d 次，接受当前方案", MAX_ITERATIONS)

    return END
```

创建 `agents/graph.py`：

```python
"""LangGraph StateGraph 构建 — 两阶段并行 + 降配循环。

图拓扑：
  START → info + recommend (并行扇出)
        → planning → budget
        → should_replan (条件边)
          ├─ "planning" → planning (降配循环)
          └─ END → START
"""
from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from agents.budget import create_budget_agent
from agents.info import create_info_agent
from agents.planning import create_planning_agent
from agents.recommend import create_recommend_agent
from agents.supervisor import should_replan
from core.state import TravelState
from mcp_servers.amap.client import AmapClient
from mcp_servers.scenic.client import ScenicClient
from mcp_servers.weather.client import WeatherClient


def build_graph(
    weather_client: WeatherClient,
    scenic_client: ScenicClient,
    planning_llm: ChatOpenAI,
    budget_llm: ChatOpenAI,
) -> Any:
    """构建并编译 LangGraph 多Agent协作图。

    Args:
        weather_client: 和风天气客户端
        scenic_client: 景点数据客户端
        planning_llm: 规划用 LLM（豆包）
        budget_llm: 预算用 LLM（豆包）

    Returns:
        编译后的 LangGraph 可执行图
    """
    # 创建 Agent 节点
    info_agent = create_info_agent(weather_client, scenic_client)
    recommend_agent = create_recommend_agent(scenic_client)
    planning_agent = create_planning_agent(planning_llm)
    budget_agent = create_budget_agent(budget_llm)

    # 构建图
    graph = StateGraph(TravelState)

    # 添加节点
    graph.add_node("info", info_agent)
    graph.add_node("recommend", recommend_agent)
    graph.add_node("planning", planning_agent)
    graph.add_node("budget", budget_agent)

    # 阶段1: START → info + recommend 并行
    graph.add_edge(START, "info")
    graph.add_edge(START, "recommend")

    # 阶段2: info + recommend → planning（汇合后执行规划）
    graph.add_edge("info", "planning")
    graph.add_edge("recommend", "planning")

    # planning → budget
    graph.add_edge("planning", "budget")

    # budget → 条件路由
    graph.add_conditional_edges("budget", should_replan, {
        "planning": "planning",  # 超支则重新规划
        END: END,                # 预算内或迭代上限则结束
    })

    return graph.compile()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/agents/test_supervisor.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 类型检查 + lint**

```bash
mypy agents/supervisor.py agents/graph.py tests/agents/test_supervisor.py
ruff check agents/supervisor.py agents/graph.py tests/agents/test_supervisor.py
```

- [ ] **Step 6: Commit**

```bash
git add agents/supervisor.py agents/graph.py tests/agents/test_supervisor.py
git commit -m "feat: add Supervisor with two-phase parallel graph and budget replan loop"
```

---

## Task 9：端到端集成测试

**Files:**
- Test: `tests/agents/test_e2e.py`

用 mock 的 client 和 LLM 验证完整协作链路。

- [ ] **Step 1: 写测试**

创建 `tests/agents/test_e2e.py`：

```python
"""端到端集成测试 — 验证完整 Agent 协作链路。"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.graph import build_graph
from mcp_servers.scenic.client import ScenicClient


@pytest.fixture
def mock_weather_client() -> MagicMock:
    client = MagicMock()
    client.search_city = AsyncMock(return_value={"location_id": "101210101", "name": "杭州"})
    client.get_current_weather = AsyncMock(return_value={
        "temp_c": 28, "text": "多云", "humidity": 65, "wind_dir": "南风",
    })
    client.get_forecast = AsyncMock(return_value=[
        {"date": "2026-07-05", "temp_max": 32, "temp_min": 24, "text_day": "多云"},
    ])
    return client


@pytest.fixture
def mock_scenic_client() -> ScenicClient:
    """用真实 ScenicClient 加载 Mock 数据（不需要网络）。"""
    from pathlib import Path
    return ScenicClient(
        data_path=str(Path(__file__).parent.parent.parent / "data" / "mock" / "scenic_spots.json")
    )


@pytest.fixture
def mock_planning_llm() -> MagicMock:
    """模拟豆包 LLM 返回行程 JSON。"""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(
        content=json.dumps({
            "destination": "杭州",
            "days": [{
                "date": "2026-07-05",
                "spots": [{
                    "spot_id": "hangzhou-xihu", "name": "西湖",
                    "arrival_time": "09:00", "departure_time": "12:00",
                    "duration_hours": 3.0, "ticket_price": 0,
                    "category": "自然风光", "location": "120.141,30.246",
                }],
                "meals": [{"name": "午餐", "meal_type": "lunch", "estimated_cost": 50}],
                "transport": [],
                "total_cost": 50,
            }],
            "total_cost": 50,
            "weather_summary": "多云 24-32度",
            "budget": None,
        }, ensure_ascii=False)
    ))
    return llm


@pytest.fixture
def mock_budget_llm() -> MagicMock:
    llm = MagicMock()
    return llm


async def test_e2e_within_budget(
    mock_weather_client: MagicMock,
    mock_scenic_client: ScenicClient,
    mock_planning_llm: MagicMock,
    mock_budget_llm: MagicMock,
) -> None:
    """完整流程：输入需求 → 输出行程，预算内。"""
    graph = build_graph(
        weather_client=mock_weather_client,
        scenic_client=mock_scenic_client,
        planning_llm=mock_planning_llm,
        budget_llm=mock_budget_llm,
    )

    initial_state = {
        "messages": [],
        "destination": "杭州",
        "dates": {"start": "2026-07-05", "end": "2026-07-06"},
        "budget_total": 500,
        "preferences": ["自然风光", "美食"],
        "iteration_count": 0,
    }

    result = await graph.ainvoke(initial_state)

    assert "itinerary" in result
    assert result["itinerary"].destination == "杭州"
    assert len(result["itinerary"].days) == 1
    assert result["itinerary"].days[0].spots[0].name == "西湖"
    assert "budget_breakdown" in result
    assert result["budget_breakdown"].over_budget is False


async def test_e2e_weather_degradation(
    mock_scenic_client: ScenicClient,
    mock_planning_llm: MagicMock,
    mock_budget_llm: MagicMock,
) -> None:
    """天气查询失败时行程仍可生成（优雅降级）。"""
    weather_client = MagicMock()
    weather_client.search_city = AsyncMock(side_effect=Exception("API error"))

    graph = build_graph(
        weather_client=weather_client,
        scenic_client=mock_scenic_client,
        planning_llm=mock_planning_llm,
        budget_llm=mock_budget_llm,
    )

    initial_state = {
        "messages": [],
        "destination": "杭州",
        "dates": {"start": "2026-07-05", "end": "2026-07-06"},
        "budget_total": 500,
        "preferences": [],
        "iteration_count": 0,
    }

    result = await graph.ainvoke(initial_state)

    # 天气失败但行程仍应生成
    assert "itinerary" in result
    assert result["itinerary"].destination == "杭州"
```

- [ ] **Step 2: 运行测试**

Run: `pytest tests/agents/test_e2e.py -v`
Expected: PASS (2 tests)

> 如果 LangGraph 的并行节点+状态合并有报错，需要调整 graph.py 中的边连接方式。LangGraph 要求 fan-out 的多个节点都完成后才会执行 fan-in 节点。

- [ ] **Step 3: 类型检查 + lint**

```bash
mypy tests/agents/test_e2e.py
ruff check tests/agents/test_e2e.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/agents/test_e2e.py
git commit -m "test: add end-to-end integration tests for multi-agent collaboration"
```

---

## Task 10：CLI 演示脚本

**Files:**
- Create: `scripts/demo_chat.py`

- [ ] **Step 1: 实现 CLI 脚本**

创建 `scripts/demo_chat.py`：

```python
"""TourSwarm CLI 演示脚本 — 命令行体验多Agent协作。

用法：
    python -m scripts.demo_chat "周末去杭州2日游预算500元喜欢美食"
    python -m scripts.demo_chat  # 交互模式
"""
import asyncio
import json
import sys

from agents.graph import build_graph
from core.config.settings import get_settings
from core.llm import create_llm
from mcp_servers.amap.client import AmapClient
from mcp_servers.scenic.client import ScenicClient
from mcp_servers.weather.client import WeatherClient


def parse_input(text: str) -> dict:
    """简单解析用户输入为 TravelState 初始字段。

    MVP阶段用关键词匹配，Phase 2后续可换成LLM意图识别。
    """
    destination = "杭州"  # 默认
    budget = 500

    # 简单提取预算
    for word in text.split():
        if "元" in word:
            try:
                budget = int(word.replace("元", "").replace("预算", ""))
            except ValueError:
                pass

    # 简单提取目的地
    cities = ["杭州", "北京", "上海", "南京", "苏州", "成都", "西安", "厦门"]
    for city in cities:
        if city in text:
            destination = city
            break

    # 简单提取偏好
    preferences = []
    pref_keywords = {
        "美食": ["美食", "吃", "小吃"],
        "自然风光": ["自然", "山水", "湖", "风景"],
        "人文古迹": ["历史", "古迹", "寺庙", "博物馆"],
        "购物": ["购物", "逛街", "买"],
    }
    for pref, keywords in pref_keywords.items():
        if any(kw in text for kw in keywords):
            preferences.append(pref)

    return {
        "destination": destination,
        "budget_total": budget,
        "preferences": preferences,
        "dates": {"start": "2026-07-05", "end": "2026-07-06"},
    }


async def run_demo(user_input: str) -> None:
    """运行演示。"""
    settings = get_settings()

    # 初始化 clients
    weather_client = WeatherClient(api_key=settings.qweather_api_key)
    scenic_client = ScenicClient(data_path="data/mock/scenic_spots.json")

    # 初始化 LLM
    planning_llm = create_llm("doubao:Doubao-Seed-2.0-pro")
    budget_llm = create_llm("doubao:Doubao-Seed-2.0-pro")

    # 构建图
    graph = build_graph(
        weather_client=weather_client,
        scenic_client=scenic_client,
        planning_llm=planning_llm,
        budget_llm=budget_llm,
    )

    # 解析输入
    parsed = parse_input(user_input)

    print("\n" + "=" * 60)
    print("🎯 TourSwarm 智能旅游助手")
    print("=" * 60)
    print(f"📍 目的地: {parsed['destination']}")
    print(f"💰 预算: {parsed['budget_total']}元")
    print(f"🏷️  偏好: {', '.join(parsed['preferences']) or '无'}")
    print(f"📅 日期: {parsed['dates']['start']} ~ {parsed['dates']['end']}")
    print("-" * 60)
    print("⏳ Agent 协作中...\n")

    # 执行
    initial_state = {
        "messages": [],
        "iteration_count": 0,
        **parsed,
    }

    result = await graph.ainvoke(initial_state)

    # 输出结果
    print("=" * 60)
    print("✅ 行程方案")
    print("=" * 60)

    itinerary = result.get("itinerary")
    if itinerary:
        print(f"\n📍 {itinerary.destination} · {len(itinerary.days)}天行程")
        print(f"🌤️  天气: {itinerary.weather_summary}")
        print(f"💰 总花费: {itinerary.total_cost}元")

        for i, day in enumerate(itinerary.days, 1):
            print(f"\n--- 第{i}天 {day.date} ---")
            for spot in day.spots:
                print(f"  🏛️  {spot.arrival_time}-{spot.departure_time} {spot.name}"
                      f" (门票{spot.ticket_price}元, {spot.duration_hours}h)")
            for meal in day.meals:
                print(f"  🍽️  {meal.name} ({meal.estimated_cost}元)")
            print(f"  💵 当日花费: {day.total_cost}元")

    budget = result.get("budget_breakdown")
    if budget:
        print(f"\n--- 预算 ---")
        print(f"  总预算: {budget.total}元 | 已花费: {budget.spent}元"
              f" | {'⚠️ 超支!' if budget.over_budget else '✅ 预算内'}")

    print("\n" + "=" * 60)


def main() -> None:
    """入口。"""
    if len(sys.argv) > 1:
        user_input = " ".join(sys.argv[1:])
    else:
        print("TourSwarm 智能旅游助手 (输入 q 退出)")
        user_input = input("请输入需求: ")
        if user_input.strip().lower() == "q":
            return

    asyncio.run(run_demo(user_input))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 手动验证（需要真实 LLM Key）**

```bash
# 确保 .env 已配置豆包/DeepSeek Key
python -m scripts.demo_chat "周末去杭州2日游预算500元喜欢美食"
```

预期输出：打印目的地/预算/偏好 → Agent执行 → 输出结构化行程

- [ ] **Step 3: Commit**

```bash
git add scripts/demo_chat.py
git commit -m "feat: add CLI demo script for multi-agent collaboration showcase"
```

---

## Task 11：里程碑 M2 验收

- [ ] **Step 1: 全量测试 + 类型检查 + lint**

Run: `bash scripts/check.sh`
Expected: ruff 无违规 / mypy 无错误 / pytest 全部 PASS

- [ ] **Step 2: 检查覆盖率**

Run: `pytest tests/ --cov=agents --cov=core --cov=models --cov-report=term-missing`
Expected: agents 模块覆盖率 ≥ 75%

- [ ] **Step 3: CLI 演示验证**

```bash
python -m scripts.demo_chat "周末去杭州2日游预算500元喜欢美食"
```
Expected: 输出包含目的地/天气/景点/预算的完整行程方案

- [ ] **Step 4: 更新总路线图进度**

在 `docs/plans/00-MASTER-ROADMAP.md` 中勾选 Phase 2 完成。

- [ ] **Step 5: 撰写里程碑记录**

在 Obsidian 知识库 `03_项目/tourswarm/日报/` 下创建 M2 验收记录。

- [ ] **Step 6: Commit 里程碑**

```bash
git add docs/
git commit -m "milestone: M2 complete — multi-agent collaboration with two-phase parallel graph"
```

---

## Phase 2 完成标准（M2 验收清单）

- [ ] TravelState 定义完整，messages 使用 add_messages reducer
- [ ] 4个 Agent 各有独立实现和单元测试
- [ ] Supervisor 两阶段并行图编译成功
- [ ] 预算超支自动降配循环（最大3次）正常工作
- [ ] 端到端测试通过（mock client + mock LLM）
- [ ] CLI 演示脚本可用真实 LLM 生成行程
- [ ] 行程输出为 Pydantic Itinerary 模型（可序列化 JSON）
- [ ] 天气查询失败时优雅降级，不阻断行程生成
- [ ] mypy strict 无错误
- [ ] ruff lint 无违规
- [ ] agents 模块覆盖率 ≥ 75%
