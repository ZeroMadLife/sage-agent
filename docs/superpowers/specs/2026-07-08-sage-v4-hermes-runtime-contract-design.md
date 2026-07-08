# Sage v4 设计 — Hermes-style Runtime Contract

> 日期：2026-07-08
> 项目代号：Sage
> 状态：设计草案，待实施计划
> 前置：v3 已完成 prompt caching、approval、tool registry、tool_search、run/session history、Hermes WebUI 风格工作台细节

---

## 1. 背景

Sage 已经从 TourSwarm 的旅游 Agent 重定位为个人网页端 Coding Agent。v1 到 v3 完成了核心迁移：

- `core/coding/` 拥有独立 runtime、engine、workspace、tools、skills、todo、plan、worker、run/session store。
- Web 侧已有三栏工作台、文件树、git badge、Skills、MCP 可见性、模型切换、approval diff、run/session history。
- 旅游能力已经降级为 Sage 的 domain skill / benchmark scenario，不再作为主产品叙事。

下一阶段目标不是继续堆 UI 功能，而是让 Sage 的运行时边界更接近 Hermes Agent + Hermes WebUI 的成熟形态：

> **把 Sage 从“能跑的网页 coding agent”推进到“有稳定 runtime contract 的个人 coding workbench”。**

Hermes 的关键价值不只是界面，而是它把一次 agent turn 拆成稳定的运行时事件、工具执行、审批、取消、写回、恢复和前端归约。Sage v4 先学习这套“骨架”，不做完整复制。

---

## 2. 当前 Sage 架构判断

### 2.1 后端现状

当前主链路是：

```text
api/coding.py
  └─ CodingRuntime.run_turn()
       └─ Engine.run_turn()
            ├─ ContextManager.build()
            ├─ model.complete()
            ├─ parse(model_output)
            ├─ PermissionChecker / ToolPolicyChecker / ApprovalManager
            ├─ RegisteredTool.execute()
            └─ yield dict event
```

已具备的地基：

- `CodingRuntime` 持有 session 级状态、tools、activated_tools、approval、todo、plan、worker、run store。
- `Engine` 是 async generator，能流式产出 `model_requested`、`tool_call`、`tool_result`、`final` 等事件。
- `RunStore` 会持久化 trace，并派生 timeline。
- `SessionStore` 支持 session history、resume 和 message replay。
- tools 已经装饰器化，并支持 `tool_search` 延迟加载。

主要问题：

- 事件还是松散 `dict`，没有统一 `RunEvent` contract。
- `Engine` 同时负责模型循环、工具权限、审批等待、工具执行和 event 构造，职责开始变宽。
- `RunStore`、WebSocket、前端 `CodingServerEvent` 类型依赖“约定中的 dict shape”，缺少后端源头约束。
- stop/cancel 有最小闭环，但没有 Hermes 那种 stream ownership、active run registry、reattach 语义。

### 2.2 前端现状

当前主链路是：

```text
CodingView.vue
  └─ useCodingStore()
       ├─ WebSocket lifecycle
       ├─ server event handling
       ├─ messages/tool activity aggregation
       ├─ approval polling
       ├─ session/run switching
       ├─ file tree cache
       └─ git/model/skills/mcp loading
```

已具备的地基：

- 三栏 Sage 工作台。
- tool activity 折叠与摘要。
- approval card + diff modal。
- session/run list 和 run timeline。
- file tree cache 与 git refresh。

主要问题：

- `frontend/src/stores/coding.ts` 开始承担过多责任。
- WebSocket lifecycle、事件归约、workspace 数据加载、approval polling 混在一个 store。
- 后续如果要做 reconnect / reattach / live run recovery，这个 store 会迅速变脆。

---

## 3. Hermes 对照点

### 3.1 Hermes Agent 后端值得学习的部分

参考：

- `hermes-agent/agent/conversation_loop.py`
- `hermes-agent/agent/tool_executor.py`
- `hermes-agent/agent/system_prompt.py`
- `hermes-agent/agent/prompt_caching.py`
- `hermes-agent/tools/approval.py`

Sage v4 只学习以下设计：

1. **turn lifecycle 明确化**
   - 一次用户请求有清楚的 started / model / tool / final / cancelled / finished 事件。
   - 每个事件能被持久化、回放、前端归约。

2. **tool executor 独立化**
   - 工具执行不是 engine 的附属 if/else，而是一层独立边界。
   - 这一层负责 permission、policy、approval、timeout、result normalization、event emission。

3. **运行时写回可审计**
   - run trace 不是调试日志，而是 UI、恢复、评测和面试讲述都能复用的事实来源。

4. **prompt / tool / context 变化有明确失效点**
   - v3 已有 prompt caching。
   - v4 要让 tool activation、skill injection、context invalidation 更清楚地进入 contract。

### 3.2 Hermes WebUI 前端值得学习的部分

参考：

- `hermes-webui/api/streaming.py`
- `hermes-webui/static/messages.js`
- `hermes-webui/static/sessions.js`
- `hermes-webui/static/workspace.js`
- `hermes-webui/static/ui.js`
- `hermes-webui/ARCHITECTURE.md`

Sage v4 只学习以下设计：

1. **stream ownership**
   - 只有当前 session/current pane 能接管当前流。
   - session switch 时不能让旧 run 覆盖新界面。

2. **事件归约**
   - 前端应有纯函数把 server event 归约成 UI state。
   - WebSocket 连接管理不应和 message/tool rendering 细节耦合。

3. **worklog 是 metadata，不是主消息**
   - 工具调用和 run timeline 不抢 assistant final 的层级。
   - 裸 JSON 保留在 trace，不直接暴露在主 UI。

4. **恢复优先于炫技**
   - 下一阶段 UI 不先重画视觉，而是让 session switch、run list、trace replay 和后续 reattach 有稳定基础。

---

## 4. v4 目标

Sage v4 的目标是建立一套稳定的 runtime contract，让后续 Hermes 化可以逐层推进。

### 4.1 后端目标

1. 新增 `core/coding/events.py`，定义强类型 `RunEvent` 系列。
2. 让 `Engine` 和 `CodingRuntime` 产出的事件经过统一构造函数或 Pydantic schema。
3. 新增 `core/coding/tool_executor.py`，从 `Engine._execute_tool_payload()` 中拆出工具执行边界。
4. `RunStore` 持久化和 timeline 派生继续兼容旧事件 shape，但内部优先使用 typed event。
5. WebSocket 继续发送 JSON，不改变 API 路径。

### 4.2 前端目标

1. 把 `stores/coding.ts` 中的 server event 处理拆成独立归约层。
2. 新增 `frontend/src/stores/codingEvents.ts` 或同等文件，集中处理 `CodingServerEvent -> state mutation`。
3. 新增轻量 stream controller，隔离 WebSocket connect/disconnect/session switch。
4. 保持现有 UI 外观和交互基本不变。

### 4.3 产品目标

1. Sage 的面试叙事从“我做了工具调用 UI”升级为“我设计了可审计、可恢复、可扩展的 agent runtime contract”。
2. 旅游 skill 继续作为多领域扩展和 benchmark 证据保留，不进入 v4 主改动。
3. v4 为 v4.2 的 live run recovery / stream reattach 打基础，但不强行一次做完。

---

## 5. 非目标

本轮明确不做：

- 不移植 Hermes provider profile 系统。
- 不移植 Hermes plugin/gateway/cron/多平台消息接入。
- 不移植 Hermes sandbox/bubblewrap。
- 不重写 LLM provider 层，继续使用现有 `core/llm.py`。
- 不大改视觉设计，不做 Hermes WebUI 的 vanilla JS 架构迁移。
- 不重做旅游 Agent，不改 `agents/`、`mcp_servers/`、`core/verifier.py`、`evals/`。
- 不做完整 live run reattach；只让事件契约和前端分层为它做好准备。
- 不把所有事件一次性改成复杂继承体系，避免过度工程。

---

## 6. 目标架构

### 6.1 后端目标形态

```text
api/coding.py
  └─ CodingRuntime.run_turn()
       ├─ creates run_id
       ├─ emits TurnStartedEvent
       ├─ Engine.run_turn()
       │    ├─ model loop
       │    ├─ parse output
       │    └─ ToolExecutor.execute()
       │         ├─ PermissionChecker
       │         ├─ ToolPolicyChecker
       │         ├─ ApprovalManager wait
       │         ├─ RegisteredTool.execute()
       │         └─ ToolCall/ToolResult events
       ├─ RunStore.append_event()
       ├─ SessionEventBus.emit()
       └─ websocket sends event.model_dump()
```

### 6.2 前端目标形态

```text
CodingView.vue
  └─ useCodingStore()
       ├─ codingStream.ts
       │    ├─ connect(sessionId)
       │    ├─ send(content)
       │    ├─ stop()
       │    └─ disconnect()
       ├─ codingEvents.ts
       │    └─ applyCodingEvent(state, event)
       └─ codingWorkspace loaders
            ├─ sessions/runs
            ├─ files/git
            └─ skills/models/mcp
```

v4 不要求一次拆到完美模块，只要求把事件归约和 WebSocket 生命周期从大 store 中切出第一条清晰边界。

---

## 7. RunEvent Contract

### 7.1 事件设计原则

- 每个 event 必须有 `type`。
- run 内事件必须有 `run_id`，除非是 WebSocket 建连错误。
- 可持久化，必须是 JSON-safe。
- 前端不依赖 Python 类名，只依赖稳定 `type` 和字段。
- 新字段可以增加，旧字段不能随意改名。

### 7.2 首批事件

```text
turn_started
model_requested
model_parsed
tool_call
approval_required
approval_granted
tool_result
retry
final
step_limit
cancelled
error
turn_finished
runtime_mode_changed
stop_requested
skill_invoked
```

### 7.3 事件字段草案

核心字段：

```python
class BaseRunEvent(BaseModel):
    type: str
    run_id: str
    created_at: str
```

工具事件：

```python
class ToolCallEvent(BaseRunEvent):
    type: Literal["tool_call"]
    tool: str
    args: dict[str, Any]

class ToolResultEvent(BaseRunEvent):
    type: Literal["tool_result"]
    tool: str
    args: dict[str, Any]
    content: str
    is_error: bool
    policy_reason: str | None = None
    security_event_type: str | None = None
```

审批事件：

```python
class ApprovalRequiredEvent(BaseRunEvent):
    type: Literal["approval_required"]
    approval_id: str
    tool: str
    args: dict[str, Any]
    description: str
    pattern_key: str
```

终止事件：

```python
class FinalEvent(BaseRunEvent):
    type: Literal["final"]
    content: str

class CancelledEvent(BaseRunEvent):
    type: Literal["cancelled"]
    content: str
```

实现时可以用 Pydantic 模型，也可以用 typed factory 函数逐步过渡。第一版重点是统一构造和测试，不追求类型体系华丽。

---

## 8. ToolExecutor 边界

### 8.1 当前问题

`Engine._execute_tool_payload()` 同时做：

- stop 检查
- payload normalization
- unknown tool error
- permission check
- policy check
- approval wait
- tool_call event
- sync tool execution
- tool_result event
- history append

这让 Engine 越来越不像“模型循环”，而像一个小型 runtime。

### 8.2 v4 拆分

新增 `core/coding/tool_executor.py`，核心接口约束如下：

- 构造依赖：`tools`、`workspace`、`permission_checker`、`policy_checker`、`approval_manager`、`session_id`、`should_stop`。
- 输入：模型产出的原始工具 payload。
- 输出：按顺序 yield 标准 `RunEvent`，包括 `tool_call`、`approval_required`、`approval_granted`、`tool_result`、`cancelled`。
- 不负责：模型调用、prompt 构建、step limit、final/retry 处理、assistant history 写入。

职责：

- 把模型 payload 转成 `(name, args)`。
- 处理 unknown tool。
- 调 permission/policy。
- 触发 approval_required 并等待结果。
- 产出 tool_call/tool_result/cancelled 事件。

Engine 保留：

- history append。
- model call。
- parse raw output。
- step limit。
- final/retry/loop control。

### 8.3 历史写入策略

短期内仍由 `Engine` 把 `tool_result` 写入 `history`，避免重构过大。ToolExecutor 返回 typed events，Engine 根据事件类型决定是否写 history。

后续 v4.2 可以把 history writer 抽成 `TurnJournal` 或 `ConversationState`。

---

## 9. 前端 Stream 分层

### 9.1 当前问题

`frontend/src/stores/coding.ts` 同时是：

- session manager
- WebSocket manager
- event reducer
- approval poller
- run/session loader
- file tree cache
- git/model/skills/mcp loader

这在 demo 阶段没问题，但一旦做 Hermes 的 reconnect、dedupe、stream ownership，会很难维护。

### 9.2 v4 拆分策略

第一刀只拆两层：

1. `frontend/src/stores/codingEvents.ts`
   - 导出 `applyCodingEvent(state, event)`。
   - 负责 model/tool/final/error/approval 等事件如何改变消息、工具活动、thinking、context、pending approval。

2. `frontend/src/stores/codingStream.ts`
   - 封装 WebSocket。
   - 负责 connect、send、close、onmessage、onerror。
   - 不直接懂 UI 结构，只把事件交给 callback。

`coding.ts` 继续保留 Pinia store，但职责收敛为：

- 管理 state。
- 调用 stream controller。
- 调用 API loaders。
- 暴露 UI actions。

### 9.3 stream ownership 最小规则

v4 只做轻量规则：

- 每次收到 event 时，必须确认 event 属于当前 `sessionId` 或当前 socket。
- session switch 时关闭旧 socket，并清空当前 live run 状态。
- 旧 socket 的 late event 不允许写入新 session 的消息列表。

完整 last-event-id / replay / reattach 放到 v4.2。

---

## 10. 测试策略

### 10.1 后端测试

新增或调整：

- `tests/core/coding/test_events.py`
  - event factory/model 能生成 JSON-safe dict。
  - tool/final/cancelled 等关键事件字段稳定。

- `tests/core/coding/test_tool_executor.py`
  - unknown tool 返回 tool_result error。
  - permission denied 返回带 `security_event_type` 的 tool_result。
  - policy denied 返回带 `policy_reason` 的 tool_result。
  - approval_required -> approval_granted -> tool_call -> tool_result 顺序正确。
  - stop 时返回 cancelled。

- `tests/core/coding/test_engine.py`
  - 原有 engine 行为不变。
  - Engine 使用 ToolExecutor 后事件顺序不变。

- `tests/api/test_coding_routes.py`
  - WebSocket 事件 shape 与旧前端兼容。

### 10.2 前端测试

新增或调整：

- `frontend/src/stores/codingEvents.test.ts`
  - apply model_requested 更新 context。
  - apply tool_call 新建/追加 tool activity。
  - apply tool_result 更新对应 tool 状态。
  - apply final/cancelled 收束 thinking message。
  - apply error 记录错误并停止 thinking。

- `frontend/src/stores/codingStream.test.ts`
  - connect 使用正确 URL。
  - send 只在 open 状态发送。
  - close 后不继续发送。

- 保持现有 `coding.test.ts`、组件测试全绿。

### 10.3 全量门槛

每个实现任务完成后至少跑：

```bash
pytest tests/core/coding tests/api/test_coding_routes.py -q
cd frontend && npm run test -- --run
```

最终合入前跑：

```bash
bash scripts/check.sh
cd frontend && npm run build
```

---

## 11. 分阶段实施建议

### v4.1 Runtime Contract

目标：后端事件契约 + ToolExecutor + 前端事件归约。

任务：

1. 新增 `core/coding/events.py` 和测试。
2. 新增 `core/coding/tool_executor.py`，先复制 Engine 工具执行逻辑再收敛。
3. Engine 接入 ToolExecutor，保持事件顺序不变。
4. RunStore/API 兼容 typed event 输出。
5. 前端新增 `codingEvents.ts`，把 `handleServerEvent` 的核心分支迁移出去。
6. 前端新增 `codingStream.ts`，把 WebSocket 生命周期迁移出去。

### v4.2 Live Run Recovery

目标：向 Hermes 的 stream ownership / active run / reattach 靠近。

任务：

1. 后端 active run registry。
2. session 级 latest run 查询。
3. WebSocket reconnect 后回放 run trace。
4. 前端 session switch 防 late event。
5. run 进行中刷新页面后的恢复提示。

### v4.3 Workbench 深化

目标：更像真实 coding workbench。

任务：

1. run detail 独立右侧/底部面板。
2. file preview 与 tool_result 关联跳转。
3. approval diff 与 file preview 联动。
4. context/compact 操作实装。

---

## 12. 面试叙事

v4 可以这样讲：

> 我没有只做一个聊天 UI，而是把 coding agent 的一次运行抽象成可持久化的 RunEvent contract。模型请求、工具调用、审批、取消、最终回答都能进入同一条 trace。后端通过 ToolExecutor 把工具治理从模型循环里拆出来，前端通过事件归约把 WebSocket 流式事件变成稳定 UI 状态。这让后续做 run recovery、benchmark、工具调用成功率统计和多 skill 扩展都有统一事实来源。

量化指标方向：

- tool call success rate。
- approval wait time。
- run completion rate。
- cancelled recovery rate。
- first-pass test success。
- P95 turn latency。
- prompt cache hit proxy：同 session system prompt rebuild 次数。

---

## 13. 风险与缓解

### 风险 1：事件模型过度设计

缓解：第一版只覆盖现有事件，不新增复杂状态机；event type 和字段以当前 WebSocket 输出为边界。

### 风险 2：ToolExecutor 拆分导致历史写入变化

缓解：ToolExecutor 只产出事件，Engine 仍负责 history append；先保持行为一致。

### 风险 3：前端拆分破坏现有 demo

缓解：先抽纯函数归约，组件和 UI 不动；用现有 store 测试兜底。

### 风险 4：误碰旅游侧

缓解：v4.1 文件范围限定在 `core/coding/`、`api/coding.py`、`api/schemas.py`、`frontend/src/stores/`、相关测试和 docs；不动旅游侧。

---

## 14. 完成标志

v4.1 完成时必须满足：

- `core/coding/events.py` 存在，核心事件有测试。
- `core/coding/tool_executor.py` 存在，工具执行测试覆盖 permission/policy/approval/stop。
- `Engine` 的主循环比 v3 更窄，工具执行职责迁出。
- WebSocket 事件对前端保持兼容。
- `frontend/src/stores/codingEvents.ts` 存在，事件归约有测试。
- `frontend/src/stores/codingStream.ts` 存在，WebSocket 生命周期有测试。
- 旅游侧测试仍全绿。
- `bash scripts/check.sh`、`cd frontend && npm run test -- --run`、`cd frontend && npm run build` 全绿。
