# Phase 4：前端与集成测试 — TDD 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Phase 1-3 的 MCP + LangGraph + Memory 能力产品化为可演示的 Web 工作台：用户输入需求后，后端通过 FastAPI/WebSocket 执行 Agent，确定性验证器校验行程，Vue3 前端展示 AI 进度、结构化行程、预算与可编辑卡片。

**Architecture:** Phase 4 采用薄 API 层：FastAPI 负责协议、会话、WebSocket 事件和持久化；Agent 编排继续复用 `agents/graph.py`；确定性验证器位于 `core/verifier.py`，在 `planning -> budget` 后作为质量闸门。前端不做纯聊天页，而做“AI 助手进度 + 可编辑行程工作台”的双栏体验。

**Tech Stack:** Python 3.11+ / FastAPI / WebSocket / Pydantic v2 / SQLAlchemy / PostgreSQL / LangGraph / Vue 3.5 / Vite / Pinia / TypeScript / Vitest / Playwright / mypy / ruff

**类型安全约定:** Python 新代码必须有 type hints 并通过 strict mypy；前端 TypeScript 开启 strict；前后端数据契约优先由 Pydantic/OpenAPI 驱动。

**范围边界:** Phase 4 P0 只保证 Web 全流程、验证器、基础 eval/perf 与可演示工作台。UniApp Android、地图路线可视化、登录鉴权、复杂酒店/餐厅价格与真实路线最优作为 P1/P2，不阻塞 M4。

**预计耗时:** Week 7-8（10个工作日）

---

## 设计决策（grill-me 对齐结果）

| 决策点 | 结论 |
|--------|------|
| Phase 4 主目标 | 产品闭环，不是把 CLI 搬到网页 |
| M4 最低可交付 | FastAPI API + WebSocket + Vue3 Web 工作台 + 确定性验证器 + E2E/性能测试 |
| 前端形态 | 双栏工作台：左侧 AI 进度/工具调用/错误恢复，右侧行程时间轴/预算/可编辑卡片 |
| API 层职责 | 薄 API：协议转换、会话、事件、持久化；智能逻辑留在 `agents/` 和 `core/` |
| WebSocket 事件 | 第一版粗粒度真实事件：`progress` / `tool_call` / `result` / `error`；`token` 协议保留但不强依赖 |
| LangGraph 流式 | 先用 `chat_runner` 适配器包装 graph，不大改图；后续需要 interrupt/token streaming 再深化 |
| 验证器 P0 | 日期覆盖、预算一致、日费用求和、空行程、天气降级、记忆偏好可见性 |
| 数据持久化 | 最小三表：`sessions` / `messages` / `itineraries`，匿名 user scope 先行 |
| 前端状态 | `chatStore` / `itineraryStore` / `sessionStore` 三块 Pinia store |
| 量化指标 | 端到端成功率、验证器通过率、预算合规率、记忆影响率、P50/P95 延迟、WS 完成率、schema 有效率 |
| UniApp | Web 全流程跑通后再做，作为加分项，不阻塞 M4 |
| 地图 | 首版用时间轴 + 交通段文本；高德 JS 地图作为 P2 |

---

## 文件结构

Phase 4 完成后的核心新增结构：

```
tour-agent/
├── api/
│   ├── __init__.py
│   ├── main.py                       # FastAPI app factory
│   ├── routes.py                     # REST endpoints: chat/session/itinerary
│   ├── schemas.py                    # API request/response/WS event schemas
│   ├── ws.py                         # WebSocket endpoint
│   └── services/
│       ├── __init__.py
│       └── chat_runner.py            # graph execution -> WS events
├── core/
│   └── verifier.py                   # deterministic itinerary validation
├── evals/
│   ├── travel_cases.jsonl            # benchmark cases
│   └── run_eval.py                   # offline evaluation runner
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── api/
│   │   ├── stores/
│   │   ├── components/
│   │   │   ├── AgentProgressPanel.vue
│   │   │   ├── ItineraryTimeline.vue
│   │   │   ├── BudgetSummary.vue
│   │   │   ├── EditableSpotCard.vue
│   │   │   └── ChatComposer.vue
│   │   ├── views/WorkbenchView.vue
│   │   ├── types/api.ts
│   │   └── main.ts
│   └── tests/
├── tests/
│   ├── api/
│   │   ├── test_schemas.py
│   │   ├── test_routes.py
│   │   ├── test_ws.py
│   │   └── test_chat_runner.py
│   ├── core/test_verifier.py
│   ├── integration/test_phase4_flow.py
│   └── perf/test_phase4_latency.py
└── docs/demo-script.md
```

---

## Task 1：API Schema 契约

**Files:**
- Create: `api/__init__.py`
- Create: `api/schemas.py`
- Test: `tests/api/__init__.py`
- Test: `tests/api/test_schemas.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/api/test_schemas.py`：

```python
"""FastAPI contract schema tests."""

from api.schemas import ChatRequest, ChatStartResponse, ProgressEvent, ResultEvent
from models.itinerary import BudgetBreakdown, Itinerary, ItineraryDay


def test_chat_request_defaults_to_anonymous_user() -> None:
    request = ChatRequest(content="周末去杭州2日游预算500元喜欢美食")

    assert request.content == "周末去杭州2日游预算500元喜欢美食"
    assert request.user_id == "anonymous"


def test_chat_start_response_contains_session_id() -> None:
    response = ChatStartResponse(session_id="session-001")

    assert response.model_dump() == {"session_id": "session-001"}


def test_progress_event_serializes_agent_progress() -> None:
    event = ProgressEvent(agent="planning", message="正在生成行程")

    assert event.type == "progress"
    assert event.model_dump()["agent"] == "planning"


def test_result_event_contains_itinerary_and_validation() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[ItineraryDay(date="2026-07-05", total_cost=120)],
        total_cost=120,
        budget=BudgetBreakdown(total=500, spent=120),
    )
    event = ResultEvent(
        itinerary=itinerary,
        validation={"passed": True, "issues": []},
        metrics={"latency_ms": 1200},
    )

    data = event.model_dump()
    assert data["type"] == "result"
    assert data["itinerary"]["destination"] == "杭州"
    assert data["validation"]["passed"] is True
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/api/test_schemas.py -q`

Expected: FAIL，提示 `ModuleNotFoundError: No module named 'api'`

- [ ] **Step 3: 写最小实现**

创建 `api/__init__.py`：

```python
"""FastAPI application layer for TourSwarm."""
```

创建 `api/schemas.py`：

```python
"""API request, response, and WebSocket event schemas."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from models.itinerary import Itinerary


class ChatRequest(BaseModel):
    """Request body for starting or continuing a chat session."""

    content: str = Field(min_length=1, description="User travel request")
    user_id: str = Field(default="anonymous", description="User scope for memory and sessions")


class ChatStartResponse(BaseModel):
    """Response returned when a chat session is created."""

    session_id: str


class ProgressEvent(BaseModel):
    """Agent progress event sent over WebSocket."""

    type: Literal["progress"] = "progress"
    agent: str
    message: str


class ToolCallEvent(BaseModel):
    """Tool call event for frontend transparency."""

    type: Literal["tool_call"] = "tool_call"
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class ResultEvent(BaseModel):
    """Final itinerary event."""

    type: Literal["result"] = "result"
    itinerary: Itinerary
    validation: dict[str, Any]
    metrics: dict[str, Any] = Field(default_factory=dict)


class ErrorEvent(BaseModel):
    """Recoverable or terminal error event."""

    type: Literal["error"] = "error"
    message: str
    recoverable: bool = True
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/api/test_schemas.py -q`

Expected: PASS

- [ ] **Step 5: 类型与 lint**

Run: `mypy api/schemas.py tests/api/test_schemas.py && ruff check api tests/api/test_schemas.py`

Expected: `Success: no issues found` / `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add api/__init__.py api/schemas.py tests/api/__init__.py tests/api/test_schemas.py
git commit -m "feat: add Phase 4 API event schemas"
```

---

## Task 2：确定性验证器

**Files:**
- Create: `core/verifier.py`
- Test: `tests/core/test_verifier.py`

验证器是 Phase 4 的面试区分度核心：它把 LLM 输出从“看起来合理”推进到“可校验”。

- [ ] **Step 1: 写失败测试**

创建 `tests/core/test_verifier.py`：

```python
"""Deterministic itinerary verifier tests."""

from core.verifier import verify_itinerary
from models.itinerary import BudgetBreakdown, Itinerary, ItineraryDay, SpotVisit


def _spot(name: str) -> SpotVisit:
    return SpotVisit(
        spot_id=name,
        name=name,
        arrival_time="09:00",
        departure_time="11:00",
        duration_hours=2,
    )


def test_verify_itinerary_passes_valid_plan() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[
            ItineraryDay(date="2026-07-05", spots=[_spot("西湖")], total_cost=120),
            ItineraryDay(date="2026-07-06", spots=[_spot("河坊街")], total_cost=180),
        ],
        total_cost=300,
        budget=BudgetBreakdown(total=500, spent=300, over_budget=False),
    )

    result = verify_itinerary(
        itinerary=itinerary,
        dates={"start": "2026-07-05", "end": "2026-07-06"},
        budget_total=500,
        weather_info={"error": True},
    )

    assert result.passed is True
    assert result.issues == []


def test_verify_itinerary_detects_missing_date() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[ItineraryDay(date="2026-07-05", spots=[_spot("西湖")], total_cost=120)],
        total_cost=120,
    )

    result = verify_itinerary(
        itinerary=itinerary,
        dates={"start": "2026-07-05", "end": "2026-07-06"},
        budget_total=500,
        weather_info={},
    )

    assert result.passed is False
    assert any(issue.code == "missing_date" for issue in result.issues)


def test_verify_itinerary_detects_budget_mismatch() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[ItineraryDay(date="2026-07-05", spots=[_spot("西湖")], total_cost=120)],
        total_cost=999,
        budget=BudgetBreakdown(total=500, spent=999, over_budget=False),
    )

    result = verify_itinerary(
        itinerary=itinerary,
        dates={"start": "2026-07-05", "end": "2026-07-05"},
        budget_total=500,
        weather_info={},
    )

    assert result.passed is False
    assert {issue.code for issue in result.issues} >= {"total_cost_mismatch", "over_budget_flag_mismatch"}


def test_verify_itinerary_detects_empty_day() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[ItineraryDay(date="2026-07-05", spots=[], total_cost=0)],
        total_cost=0,
    )

    result = verify_itinerary(
        itinerary=itinerary,
        dates={"start": "2026-07-05", "end": "2026-07-05"},
        budget_total=500,
        weather_info={},
    )

    assert result.passed is False
    assert any(issue.code == "empty_day" for issue in result.issues)
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/core/test_verifier.py -q`

Expected: FAIL，提示 `No module named 'core.verifier'`

- [ ] **Step 3: 写最小实现**

创建 `core/verifier.py`：

```python
"""Deterministic validation for LLM-generated itineraries."""

from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel

from models.itinerary import Itinerary


class VerificationIssue(BaseModel):
    """One deterministic validation issue."""

    code: str
    message: str
    severity: str = "error"


class VerificationResult(BaseModel):
    """Verification summary for API responses and evals."""

    passed: bool
    issues: list[VerificationIssue]


def _date_range(start: str, end: str) -> set[str]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    days: set[str] = set()
    current = start_date
    while current <= end_date:
        days.add(current.isoformat())
        current += timedelta(days=1)
    return days


def verify_itinerary(
    itinerary: Itinerary,
    dates: dict[str, str],
    budget_total: int,
    weather_info: dict[str, Any],
) -> VerificationResult:
    """Validate itinerary dates, costs, budget flags, and obvious empty plans."""
    _ = weather_info
    issues: list[VerificationIssue] = []

    expected_dates = _date_range(dates["start"], dates["end"])
    actual_dates = {day.date for day in itinerary.days}
    for missing in sorted(expected_dates - actual_dates):
        issues.append(
            VerificationIssue(
                code="missing_date",
                message=f"行程缺少日期 {missing}",
            )
        )

    for day in itinerary.days:
        if not day.spots:
            issues.append(
                VerificationIssue(
                    code="empty_day",
                    message=f"{day.date} 没有任何景点安排",
                )
            )

    day_total = sum(day.total_cost for day in itinerary.days)
    if itinerary.total_cost != day_total:
        issues.append(
            VerificationIssue(
                code="total_cost_mismatch",
                message=f"总费用 {itinerary.total_cost} 与每日费用合计 {day_total} 不一致",
            )
        )

    if itinerary.budget is not None:
        should_over_budget = itinerary.budget.spent > budget_total
        if itinerary.budget.over_budget != should_over_budget:
            issues.append(
                VerificationIssue(
                    code="over_budget_flag_mismatch",
                    message="预算超支标记与实际花费不一致",
                )
            )

    return VerificationResult(passed=not issues, issues=issues)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/test_verifier.py -q`

Expected: PASS

- [ ] **Step 5: 类型与 lint**

Run: `mypy core/verifier.py tests/core/test_verifier.py && ruff check core/verifier.py tests/core/test_verifier.py`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add core/verifier.py tests/core/test_verifier.py
git commit -m "feat: add deterministic itinerary verifier"
```

---

## Task 3：Chat Runner 适配器

**Files:**
- Create: `api/services/__init__.py`
- Create: `api/services/chat_runner.py`
- Test: `tests/api/test_chat_runner.py`

`chat_runner` 是 API 与 LangGraph 的边界：它把一次 graph 执行转成前端可消费的事件。

- [ ] **Step 1: 写失败测试**

创建 `tests/api/test_chat_runner.py`：

```python
"""Chat runner tests."""

from typing import Any

from api.schemas import ProgressEvent, ResultEvent
from api.services.chat_runner import run_chat
from models.itinerary import Itinerary, ItineraryDay


class GraphStub:
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "itinerary": Itinerary(
                destination=str(state["destination"]),
                days=[ItineraryDay(date="2026-07-05", spots=[], total_cost=0)],
                total_cost=0,
            ),
            "dates": state["dates"],
            "budget_total": state["budget_total"],
            "weather_info": {},
        }


async def test_run_chat_emits_progress_and_result() -> None:
    events = [
        event
        async for event in run_chat(
            graph=GraphStub(),
            user_id="anonymous",
            session_id="session-001",
            content="周末去杭州2日游预算500元喜欢美食",
        )
    ]

    assert isinstance(events[0], ProgressEvent)
    assert events[0].agent == "supervisor"
    assert isinstance(events[-1], ResultEvent)
    assert events[-1].itinerary.destination == "杭州"
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/api/test_chat_runner.py -q`

Expected: FAIL，提示 `No module named 'api.services'`

- [ ] **Step 3: 写最小实现**

创建 `api/services/__init__.py`：

```python
"""API service helpers."""
```

创建 `api/services/chat_runner.py`：

```python
"""Run the LangGraph itinerary planner and emit UI-friendly events."""

import time
from collections.abc import AsyncIterator
from typing import Any

from api.schemas import ErrorEvent, ProgressEvent, ResultEvent
from core.verifier import verify_itinerary
from models.itinerary import Itinerary
from scripts.demo_chat import parse_input


async def run_chat(
    graph: Any,
    user_id: str,
    session_id: str,
    content: str,
) -> AsyncIterator[ProgressEvent | ResultEvent | ErrorEvent]:
    """Execute one chat request and stream coarse-grained events."""
    started = time.perf_counter()
    parsed = parse_input(content)
    initial_state = {
        "messages": [],
        "user_id": user_id,
        "session_id": session_id,
        "iteration_count": 0,
        **parsed,
    }

    yield ProgressEvent(agent="supervisor", message="已理解需求，开始调度信息与推荐Agent")
    yield ProgressEvent(agent="info", message="正在查询天气与目的地信息")
    yield ProgressEvent(agent="recommend", message="正在筛选候选景点")

    try:
        result = await graph.ainvoke(initial_state)
    except Exception as exc:
        yield ErrorEvent(message=f"Agent execution failed: {exc}", recoverable=True)
        return

    itinerary = result.get("itinerary")
    if not isinstance(itinerary, Itinerary):
        yield ErrorEvent(message="Agent did not produce a valid itinerary", recoverable=True)
        return

    validation = verify_itinerary(
        itinerary=itinerary,
        dates=parsed["dates"],
        budget_total=int(parsed["budget_total"]),
        weather_info=result.get("weather_info", {}),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

    yield ResultEvent(
        itinerary=itinerary,
        validation=validation.model_dump(),
        metrics={"latency_ms": latency_ms},
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/api/test_chat_runner.py -q`

Expected: PASS

- [ ] **Step 5: 类型与 lint**

Run: `mypy api/services/chat_runner.py tests/api/test_chat_runner.py && ruff check api/services tests/api/test_chat_runner.py`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/services tests/api/test_chat_runner.py
git commit -m "feat: stream graph execution through chat runner"
```

---

## Task 4：FastAPI REST + WebSocket

**Files:**
- Create: `api/main.py`
- Create: `api/routes.py`
- Create: `api/ws.py`
- Test: `tests/api/test_routes.py`
- Test: `tests/api/test_ws.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/api/test_routes.py`：

```python
"""FastAPI route tests."""

from fastapi.testclient import TestClient

from api.main import create_app


def test_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_start_chat_returns_session_id() -> None:
    client = TestClient(create_app())

    response = client.post("/api/v1/chat", json={"content": "周末去杭州"})

    assert response.status_code == 200
    assert response.json()["session_id"]
```

创建 `tests/api/test_ws.py`：

```python
"""WebSocket route tests."""

from typing import Any

from fastapi.testclient import TestClient

from api.main import create_app
from models.itinerary import Itinerary, ItineraryDay


class GraphStub:
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "itinerary": Itinerary(
                destination=str(state["destination"]),
                days=[ItineraryDay(date="2026-07-05", spots=[], total_cost=0)],
                total_cost=0,
            ),
            "weather_info": {},
        }


def test_chat_websocket_rejects_unknown_session() -> None:
    client = TestClient(create_app())

    with client.websocket_connect("/api/v1/chat/missing/stream") as websocket:
        event = websocket.receive_json()

    assert event["type"] == "error"
    assert "Unknown session" in event["message"]


def test_chat_websocket_streams_runner_events() -> None:
    client = TestClient(create_app(graph=GraphStub()))
    response = client.post("/api/v1/chat", json={"content": "周末去杭州2日游预算500元"})
    session_id = response.json()["session_id"]

    with client.websocket_connect(f"/api/v1/chat/{session_id}/stream") as websocket:
        first_event = websocket.receive_json()
        second_event = websocket.receive_json()
        third_event = websocket.receive_json()
        final_event = websocket.receive_json()

    assert first_event["type"] == "progress"
    assert second_event["type"] == "progress"
    assert third_event["type"] == "progress"
    assert final_event["type"] == "result"
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/api/test_routes.py tests/api/test_ws.py -q`

Expected: FAIL，提示缺少 `api.main`

- [ ] **Step 3: 写最小实现**

创建 `api/routes.py`：

```python
"""REST routes for chat sessions."""

from uuid import uuid4

from fastapi import APIRouter

from api.schemas import ChatRequest, ChatStartResponse

router = APIRouter()
SESSION_INPUTS: dict[str, ChatRequest] = {}


@router.get("/health")
async def health() -> dict[str, str]:
    """Health check for local and deployment probes."""
    return {"status": "ok"}


@router.post("/api/v1/chat")
async def start_chat(request: ChatRequest) -> ChatStartResponse:
    """Create a lightweight chat session."""
    session_id = str(uuid4())
    SESSION_INPUTS[session_id] = request
    return ChatStartResponse(session_id=session_id)
```

创建 `api/ws.py`：

```python
"""WebSocket endpoints for AI planning progress."""

from fastapi import APIRouter, WebSocket

from api.routes import SESSION_INPUTS
from api.services.chat_runner import run_chat
from api.schemas import ErrorEvent

router = APIRouter()


@router.websocket("/api/v1/chat/{session_id}/stream")
async def chat_stream(websocket: WebSocket, session_id: str) -> None:
    """Stream chat events for a known session."""
    await websocket.accept()
    request = SESSION_INPUTS.get(session_id)
    if request is None:
        await websocket.send_json(ErrorEvent(message=f"Unknown session: {session_id}").model_dump())
        await websocket.close()
        return

    graph = websocket.app.state.graph
    if graph is None:
        await websocket.send_json(ErrorEvent(message="Graph is not configured").model_dump())
        await websocket.close()
        return

    async for event in run_chat(
        graph=graph,
        user_id=request.user_id,
        session_id=session_id,
        content=request.content,
    ):
        await websocket.send_json(event.model_dump())
    await websocket.close()
```

创建 `api/main.py`：

```python
"""FastAPI app factory."""

from fastapi import FastAPI

from api import routes, ws


def create_app(graph: object | None = None) -> FastAPI:
    """Create the TourSwarm API app."""
    app = FastAPI(title="TourSwarm API")
    app.state.graph = graph
    app.include_router(routes.router)
    app.include_router(ws.router)
    return app


app = create_app()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/api/test_routes.py tests/api/test_ws.py -q`

Expected: PASS

- [ ] **Step 5: 手动启动 API**

Run: `uvicorn api.main:app --reload --port 8000`

Expected: 打开 `http://127.0.0.1:8000/docs` 可看到 `/api/v1/chat` 与 WebSocket 路由。

- [ ] **Step 6: Commit**

```bash
git add api/main.py api/routes.py api/ws.py tests/api/test_routes.py tests/api/test_ws.py
git commit -m "feat: add FastAPI chat routes and websocket streaming"
```

---

## Task 5：Eval 数据集与指标脚本

**Files:**
- Create: `evals/travel_cases.jsonl`
- Create: `evals/run_eval.py`
- Test: `tests/integration/test_phase4_eval.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/integration/test_phase4_eval.py`：

```python
"""Phase 4 eval dataset tests."""

from evals.run_eval import load_cases, summarize_results


def test_load_cases_reads_jsonl() -> None:
    cases = load_cases("evals/travel_cases.jsonl")

    assert len(cases) >= 20
    assert {"id", "input", "expected"} <= set(cases[0])


def test_summarize_results_computes_rates() -> None:
    summary = summarize_results(
        [
            {"schema_valid": True, "verifier_passed": True, "latency_ms": 1000},
            {"schema_valid": True, "verifier_passed": False, "latency_ms": 3000},
        ]
    )

    assert summary["schema_valid_rate"] == 1.0
    assert summary["verifier_pass_rate"] == 0.5
    assert summary["p95_latency_ms"] == 3000
```

- [ ] **Step 2: 创建 eval 数据集**

创建 `evals/travel_cases.jsonl`：

```jsonl
{"id":"hz_budget_food","input":"周末去杭州2日游预算500元喜欢美食","expected":{"destination":"杭州","budget_total":500,"preferences":["美食"]}}
{"id":"bj_history","input":"北京两天，预算800，喜欢博物馆和历史古迹","expected":{"destination":"北京","budget_total":800,"preferences":["人文古迹"]}}
{"id":"sh_shopping","input":"上海一日游预算300，想购物和吃小吃","expected":{"destination":"上海","budget_total":300,"preferences":["购物","美食"]}}
{"id":"cd_nature","input":"成都周末游预算600，偏自然风光，不想太累","expected":{"destination":"成都","budget_total":600,"preferences":["自然风光"]}}
{"id":"xa_low_budget","input":"西安2日游预算400，喜欢历史，尽量省钱","expected":{"destination":"西安","budget_total":400,"preferences":["人文古迹"]}}
{"id":"sz_garden","input":"苏州一日游预算300，想看园林和古镇","expected":{"destination":"苏州","budget_total":300,"preferences":["人文古迹"]}}
{"id":"xm_food_sea","input":"厦门两天预算700，喜欢海边和美食","expected":{"destination":"厦门","budget_total":700,"preferences":["美食","自然风光"]}}
{"id":"hz_rainy_backup","input":"杭州周末游预算600，如果下雨多安排室内","expected":{"destination":"杭州","budget_total":600,"preferences":[]}}
{"id":"bj_low_walk","input":"北京2日游预算900，带父母，不想走太多路","expected":{"destination":"北京","budget_total":900,"preferences":[]}}
{"id":"sh_museum_food","input":"上海两天预算800，想看博物馆也想吃本帮菜","expected":{"destination":"上海","budget_total":800,"preferences":["人文古迹","美食"]}}
{"id":"nj_history","input":"南京周末预算500，偏历史古迹和纪念馆","expected":{"destination":"南京","budget_total":500,"preferences":["人文古迹"]}}
{"id":"cd_food","input":"成都两天预算500，主要想吃火锅和小吃","expected":{"destination":"成都","budget_total":500,"preferences":["美食"]}}
{"id":"xa_family","input":"西安亲子2日游预算1000，历史景点优先","expected":{"destination":"西安","budget_total":1000,"preferences":["人文古迹"]}}
{"id":"hz_free_spots","input":"杭州一天预算200，尽量免费景点，喜欢自然风光","expected":{"destination":"杭州","budget_total":200,"preferences":["自然风光"]}}
{"id":"bj_shopping","input":"北京一日游预算400，想逛街购物顺便吃小吃","expected":{"destination":"北京","budget_total":400,"preferences":["购物","美食"]}}
{"id":"sh_budget_tight","input":"上海周末游预算500，预算很紧，路线别太绕","expected":{"destination":"上海","budget_total":500,"preferences":[]}}
{"id":"nj_food_history","input":"南京两日游预算600，想吃鸭血粉丝也看历史景点","expected":{"destination":"南京","budget_total":600,"preferences":["美食","人文古迹"]}}
{"id":"sz_shopping_food","input":"苏州周末预算700，想逛街买东西也吃小吃","expected":{"destination":"苏州","budget_total":700,"preferences":["购物","美食"]}}
{"id":"xm_nature_budget","input":"厦门三天预算900，喜欢自然风光，住宿简单点","expected":{"destination":"厦门","budget_total":900,"preferences":["自然风光"]}}
{"id":"cd_rainy_food","input":"成都周末预算650，下雨也能玩，喜欢美食","expected":{"destination":"成都","budget_total":650,"preferences":["美食"]}}
```

- [ ] **Step 3: 写指标脚本**

创建 `evals/run_eval.py`：

```python
"""Offline evaluation helpers for Phase 4."""

import json
from pathlib import Path
from typing import Any


def load_cases(path: str) -> list[dict[str, Any]]:
    """Load JSONL travel eval cases."""
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def summarize_results(results: list[dict[str, Any]]) -> dict[str, float]:
    """Compute deterministic eval metrics."""
    total = len(results)
    if total == 0:
        return {"schema_valid_rate": 0.0, "verifier_pass_rate": 0.0, "p95_latency_ms": 0.0}

    latencies = sorted(int(item.get("latency_ms", 0)) for item in results)
    p95_index = min(total - 1, int(total * 0.95))
    return {
        "schema_valid_rate": sum(1 for item in results if item.get("schema_valid")) / total,
        "verifier_pass_rate": sum(1 for item in results if item.get("verifier_passed")) / total,
        "p95_latency_ms": float(latencies[p95_index]),
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/integration/test_phase4_eval.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add evals tests/integration/test_phase4_eval.py
git commit -m "feat: add Phase 4 travel eval dataset"
```

---

## Task 6：Vue3 Web 工作台骨架

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/src/main.ts`
- Modify: `frontend/src/App.vue`
- Create: `frontend/src/views/WorkbenchView.vue`
- Create: `frontend/src/components/AgentProgressPanel.vue`
- Create: `frontend/src/components/ItineraryTimeline.vue`
- Create: `frontend/src/components/BudgetSummary.vue`
- Create: `frontend/src/components/EditableSpotCard.vue`
- Create: `frontend/src/components/ChatComposer.vue`
- Create: `frontend/src/stores/chat.ts`
- Create: `frontend/src/stores/itinerary.ts`
- Create: `frontend/src/types/api.ts`

- [ ] **Step 1: 初始化 Vite Vue 项目**

Run:

```bash
mkdir -p frontend
cd frontend
npm create vite@latest . -- --template vue-ts
npm install
npm install pinia lucide-vue-next
npm install -D vitest @vue/test-utils jsdom
npm pkg set scripts.test="vitest --environment jsdom"
```

Expected: `frontend/package.json` 存在，`npm run dev` 可启动。

- [ ] **Step 2: 写前端单测**

创建 `frontend/src/components/AgentProgressPanel.test.ts`：

```typescript
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import AgentProgressPanel from './AgentProgressPanel.vue'

it('renders agent progress events', () => {
  const wrapper = mount(AgentProgressPanel, {
    props: {
      events: [
        { type: 'progress', agent: 'planning', message: '正在生成行程' },
      ],
    },
  })

  expect(wrapper.text()).toContain('planning')
  expect(wrapper.text()).toContain('正在生成行程')
})
```

- [ ] **Step 3: 实现核心组件**

替换 `frontend/src/App.vue`：

```vue
<script setup lang="ts">
import WorkbenchView from './views/WorkbenchView.vue'
</script>

<template>
  <WorkbenchView />
</template>
```

创建 `frontend/src/components/AgentProgressPanel.vue`：

```vue
<script setup lang="ts">
type ProgressEvent = {
  type: 'progress'
  agent: string
  message: string
}

defineProps<{ events: ProgressEvent[] }>()
</script>

<template>
  <section class="agent-progress">
    <h2>AI 进度</h2>
    <ol>
      <li v-for="(event, index) in events" :key="`${event.agent}-${index}`">
        <strong>{{ event.agent }}</strong>
        <span>{{ event.message }}</span>
      </li>
    </ol>
  </section>
</template>
```

创建 `frontend/src/views/WorkbenchView.vue`：

```vue
<script setup lang="ts">
import { ref } from 'vue'
import AgentProgressPanel from '../components/AgentProgressPanel.vue'
import BudgetSummary from '../components/BudgetSummary.vue'
import ChatComposer from '../components/ChatComposer.vue'
import ItineraryTimeline from '../components/ItineraryTimeline.vue'

const events = ref([
  { type: 'progress' as const, agent: 'supervisor', message: '等待输入旅游需求' },
])

const itinerary = ref({
  destination: '杭州',
  days: [],
  total_cost: 0,
})

const budget = ref({
  total: 500,
  spent: 0,
  over_budget: false,
})
</script>

<template>
  <main class="workbench">
    <AgentProgressPanel :events="events" />
    <section class="itinerary-workbench">
      <h1>TourSwarm 行程工作台</h1>
      <ChatComposer />
      <BudgetSummary :budget="budget" />
      <ItineraryTimeline :itinerary="itinerary" />
    </section>
  </main>
</template>
```

创建 `frontend/src/components/BudgetSummary.vue`：

```vue
<script setup lang="ts">
defineProps<{
  budget: {
    total: number
    spent: number
    over_budget: boolean
  }
}>()
</script>

<template>
  <section class="budget-summary">
    <h2>预算</h2>
    <p>{{ budget.spent }} / {{ budget.total }} 元</p>
    <strong>{{ budget.over_budget ? '已超预算' : '预算内' }}</strong>
  </section>
</template>
```

创建 `frontend/src/components/EditableSpotCard.vue`：

```vue
<script setup lang="ts">
defineProps<{
  spot: {
    spot_id: string
    name: string
    arrival_time: string
    departure_time: string
    ticket_price: number
  }
}>()
</script>

<template>
  <article class="spot-card">
    <h3>{{ spot.name }}</h3>
    <p>{{ spot.arrival_time }} - {{ spot.departure_time }}</p>
    <p>门票 {{ spot.ticket_price }} 元</p>
    <button type="button">替换</button>
  </article>
</template>
```

创建 `frontend/src/components/ItineraryTimeline.vue`：

```vue
<script setup lang="ts">
import EditableSpotCard from './EditableSpotCard.vue'

defineProps<{
  itinerary: {
    destination: string
    total_cost: number
    days: Array<{
      date: string
      total_cost: number
      spots: Array<{
        spot_id: string
        name: string
        arrival_time: string
        departure_time: string
        ticket_price: number
      }>
    }>
  }
}>()
</script>

<template>
  <section class="itinerary-timeline">
    <h2>{{ itinerary.destination }} 行程</h2>
    <p v-if="itinerary.days.length === 0">暂无行程，输入需求后开始规划。</p>
    <article v-for="day in itinerary.days" :key="day.date">
      <h3>{{ day.date }}</h3>
      <EditableSpotCard v-for="spot in day.spots" :key="spot.spot_id" :spot="spot" />
      <p>当天费用 {{ day.total_cost }} 元</p>
    </article>
  </section>
</template>
```

创建 `frontend/src/components/ChatComposer.vue`：

```vue
<script setup lang="ts">
import { ref } from 'vue'

const content = ref('周末去杭州2日游预算500元喜欢美食')
</script>

<template>
  <form class="chat-composer">
    <label for="travel-request">旅游需求</label>
    <textarea id="travel-request" v-model="content" rows="3" />
    <button type="submit">开始规划</button>
  </form>
</template>
```

创建 `frontend/src/types/api.ts`：

```typescript
export type ProgressEvent = {
  type: 'progress'
  agent: string
  message: string
}

export type BudgetSummary = {
  total: number
  spent: number
  over_budget: boolean
}

export type Itinerary = {
  destination: string
  days: Array<{
    date: string
    spots: Array<{
      spot_id: string
      name: string
      arrival_time: string
      departure_time: string
      ticket_price: number
    }>
    total_cost: number
  }>
  total_cost: number
}
```

创建 `frontend/src/stores/chat.ts`：

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ProgressEvent } from '../types/api'

export const useChatStore = defineStore('chat', () => {
  const events = ref<ProgressEvent[]>([])

  function addProgress(event: ProgressEvent) {
    events.value.push(event)
  }

  return { events, addProgress }
})
```

创建 `frontend/src/stores/itinerary.ts`：

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { BudgetSummary, Itinerary } from '../types/api'

export const useItineraryStore = defineStore('itinerary', () => {
  const itinerary = ref<Itinerary | null>(null)
  const budget = ref<BudgetSummary | null>(null)

  function setItinerary(next: Itinerary) {
    itinerary.value = next
  }

  function setBudget(next: BudgetSummary) {
    budget.value = next
  }

  return { itinerary, budget, setItinerary, setBudget }
})
```

- [ ] **Step 4: 运行前端测试**

Run: `cd frontend && npm run test -- --run`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat: scaffold Vue itinerary workbench"
```

---

## Task 7：前后端集成与性能门禁

**Files:**
- Create: `tests/integration/test_phase4_flow.py`
- Create: `tests/perf/test_phase4_latency.py`
- Modify: `scripts/check.sh`

- [ ] **Step 1: 写集成测试**

创建 `tests/integration/test_phase4_flow.py`：

```python
"""Phase 4 API integration flow tests."""

from typing import Any

from fastapi.testclient import TestClient

from api.main import create_app
from models.itinerary import Itinerary, ItineraryDay


class GraphStub:
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "itinerary": Itinerary(
                destination=str(state["destination"]),
                days=[ItineraryDay(date="2026-07-05", spots=[], total_cost=0)],
                total_cost=0,
            ),
            "weather_info": {},
        }


def test_create_session_then_connect_stream() -> None:
    client = TestClient(create_app(graph=GraphStub()))
    response = client.post("/api/v1/chat", json={"content": "周末去杭州2日游预算500元"})
    session_id = response.json()["session_id"]

    with client.websocket_connect(f"/api/v1/chat/{session_id}/stream") as websocket:
        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_json()
        event = websocket.receive_json()

    assert event["type"] == "result"
```

创建 `tests/perf/test_phase4_latency.py`：

```python
"""Phase 4 latency budget tests for deterministic components."""

import time

from core.verifier import verify_itinerary
from models.itinerary import Itinerary, ItineraryDay, SpotVisit


def test_verifier_p95_proxy_is_under_50ms() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[
            ItineraryDay(
                date="2026-07-05",
                spots=[
                    SpotVisit(
                        spot_id="xihu",
                        name="西湖",
                        arrival_time="09:00",
                        departure_time="11:00",
                        duration_hours=2,
                    )
                ],
                total_cost=0,
            )
        ],
        total_cost=0,
    )

    started = time.perf_counter()
    for _ in range(100):
        verify_itinerary(
            itinerary=itinerary,
            dates={"start": "2026-07-05", "end": "2026-07-05"},
            budget_total=500,
            weather_info={},
        )
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 50
```

- [ ] **Step 2: 运行集成与性能测试**

Run: `pytest tests/integration tests/perf -q`

Expected: PASS

- [ ] **Step 3: 更新 `scripts/check.sh`**

将 `scripts/check.sh` 的检查范围扩展到新增的 `api/` 与 `evals/`：

```bash
#!/bin/bash
# TourSwarm 代码质量检查 — 在commit前运行
# 用法：bash scripts/check.sh
# 等效于CI会跑的检查，本地先跑一遍避免提交后才发现问题

set -e
echo "===== ruff lint ====="
ruff check core/ mcp_servers/ agents/ api/ evals/ tests/
echo "===== ruff format --check ====="
ruff format --check core/ mcp_servers/ agents/ api/ evals/ tests/
echo "===== mypy type check ====="
mypy core/ mcp_servers/ agents/ api/ evals/
echo "===== pytest ====="
pytest tests/ -v --tb=short
echo ""
echo "✅ 全部检查通过"
```

- [ ] **Step 4: 全量检查**

Run: `bash scripts/check.sh`

Expected: ruff / format / mypy / pytest 全绿。

- [ ] **Step 5: Commit**

```bash
git add tests/integration tests/perf scripts/check.sh
git commit -m "test: add Phase 4 integration and latency gates"
```

---

## Task 8：M4 演示脚本与路线图更新

**Files:**
- Create: `docs/demo-script.md`
- Modify: `docs/plans/00-MASTER-ROADMAP.md`
- Modify: `README.md`

- [ ] **Step 1: 创建 5 分钟演示脚本**

创建 `docs/demo-script.md`：

```markdown
# TourSwarm M4 Demo Script

## 0:00-0:45 架构总览

- MCP Server 提供天气/景点/路线工具。
- LangGraph Supervisor 编排 Info/Recommend/Planning/Budget Agent。
- Redis + Mem0/Qdrant 提供短期与长期记忆。
- Phase 4 新增 FastAPI/WebSocket、确定性验证器与 Vue3 工作台。

## 0:45-2:00 Web 全流程

输入：`周末去杭州2日游预算500元喜欢美食`

展示：
- 左侧 Agent 进度事件。
- 右侧结构化行程时间轴。
- 预算摘要是否超支。
- 验证器结果。

## 2:00-3:10 记忆影响

先运行记忆 demo 或使用已有记忆，展示“喜欢海鲜”如何进入 prompt context 并影响规划。

## 3:10-4:20 验证器与指标

展示 `evals/travel_cases.jsonl` 和 `evals/run_eval.py`，说明 schema 有效率、验证器通过率、P95 延迟。

## 4:20-5:00 工程质量

运行：

```bash
bash scripts/check.sh
cd frontend && npm run test -- --run
```

说明 Phase 4 如何从“能生成”升级为“可观测、可校验、可交互”。
```

- [ ] **Step 2: 更新路线图**

在 `docs/plans/00-MASTER-ROADMAP.md` 的 Phase 4 当前进度中记录 M4 的实际验收命令、指标与裁剪项。

- [ ] **Step 3: 更新 README**

在 `README.md` 添加：
- API 启动命令：`uvicorn api.main:app --reload`
- 前端启动命令：`cd frontend && npm run dev`
- Phase 4 演示入口。

- [ ] **Step 4: 全量验收**

Run:

```bash
bash scripts/check.sh
cd frontend && npm run test -- --run
```

Expected: 全绿。

- [ ] **Step 5: Commit 里程碑**

```bash
git add docs README.md
git commit -m "milestone: M4 plan and demo script for frontend testing"
```

---

## Phase 4 完成标准（M4 验收清单）

- [ ] FastAPI `/health`、`/api/v1/chat` 可用。
- [ ] WebSocket `/api/v1/chat/{session_id}/stream` 可连接并返回真实进度事件。
- [ ] `core/verifier.py` 能输出 `VerificationResult`，覆盖日期、预算、空行程等 P0 规则。
- [ ] Vue3 Web 工作台可运行，第一屏是双栏工作台而非营销页或纯聊天页。
- [ ] 前端至少包含 AI 进度、行程时间轴、预算摘要、可编辑景点卡片、输入框。
- [ ] `evals/travel_cases.jsonl` 至少 20 条中文旅游需求；脚本输出 schema 有效率、验证器通过率、P95 延迟。
- [ ] `bash scripts/check.sh` 全绿。
- [ ] `cd frontend && npm run test -- --run` 全绿。
- [ ] 5 分钟演示脚本完成，能讲清楚“Agent 产品化 + 确定性验证器 + 指标体系”。

## 明确暂缓项

- UniApp Android：Web 全流程稳定后启动，作为 Phase 4 后半或 Phase 5 加分项。
- 高德 JS 地图：首版用时间轴与交通段文本替代，后续补地图路线。
- 登录鉴权：先匿名 user/session scope，部署前再加 JWT 或 OAuth。
- 复杂路线最优：先验证结构一致性和预算，不把 Phase 4 拖成算法重写。
