# 02 - 总体架构

> 本章目标：能画出 Sage 的三层架构图，解释请求主链路每一跳的职责，并指出 Runtime、Engine、ToolExecutor、Timeline 各自的边界。

## Sage 的三层

```text
控制面：Vue -> FastAPI -> CodingRuntime / HarnessRuntimeAdapter -> Engine / create_agent -> ToolExecutor / Middleware
状态面：Session -> Memory -> KnowledgeStore -> Checkpoint -> Subagent -> Todo
证据面：Timeline SQLite -> RunStore trace -> Diff artifact -> Benchmark / Metrics
```

**控制面**决定任务怎么往前推。`api/coding.py` 接收 WebSocket 请求；`CodingRuntime`（legacy）或 `SageHarnessRuntimeAdapter`（deerflow_v2）建立一次 run 的外层生命周期；`Engine.run_turn()`（legacy）或 `create_agent`（v2）推进 model -> parse -> tool 循环；`ToolExecutor` 或 Middleware 链在执行前完成参数、权限、策略和审批检查。

**状态面**决定系统能不能接着干。`SessionStore` 保存会话历史与模式；`MemoryStore` 保存用户确认的稳定事实；`KnowledgeStore` 保存 Wiki/Source/Revision；`LangGraph checkpointer` 保存 graph 执行状态；`SubagentRegistry` 保存子代理生命周期；`TodoLedger` 保存当前任务账本。

**证据面**决定系统能不能被复盘。每次 run 都有独立目录，里面有 `trace.jsonl`、`diff.json`、`tool-results/<call_id>.txt`。`SessionEventJournal`（`timeline.sqlite3`）保存用户可见事件序列。`CompactionStore` 保存压缩 checkpoint + HMAC。`evals/coding/` 再把固定任务、fixture、verifier 和 metrics 接起来，判断改动有没有破坏 harness 行为。

## 顶层模块图

```text
┌───────────────────────────────────────────────────────────────┐
│  Vue Assistant Shell / Coding Workbench                       │
│  ├── /assistant 首页（today + composer + 摘要）               │
│  ├── /coding/session/:id 工作台（三栏 + Timeline + Inspector）│
│  └── /knowledge / /evolution / /public 知识与成长视图          │
└──────────────────────┬────────────────────────────────────────┘
                       │ WebSocket / REST
┌──────────────────────▼────────────────────────────────────────┐
│  FastAPI App (api/)                                           │
│  ├── cloud_auth.py - GitHub OAuth + HttpOnly session          │
│  ├── coding.py - REST + WebSocket handler                     │
│  └── coding_runs.py - CodingRunRegistry                       │
└──────────────────────┬────────────────────────────────────────┘
                       │
┌──────────────────────▼────────────────────────────────────────┐
│  Harness Runtime Adapter (core/harness/) - 21 个 adapter      │
│  runtime / event / approval / tool / knowledge / memory /     │
│  mcp / sandbox / subagent / evidence_bundle / ...             │
└──────────────────────┬────────────────────────────────────────┘
                       │
┌──────────────────────▼────────────────────────────────────────┐
│  sage_harness package (packages/sage_harness/) - 应用中立     │
│  ├── agents/ - create_sage_agent + factory + SageThreadState  │
│  ├── runtime/ - HarnessRunManager + checkpointer + events     │
│  ├── middleware/ - 10 个 AgentMiddleware（实际）               │
│  ├── tools/ - registry + metadata + adapters                 │
│  ├── sandbox/ - base + local                                  │
│  ├── mcp/ - manager + deferred                                │
│  ├── subagents/ - registry + executor + events                │
│  ├── context/ - durable + summarization                       │
│  ├── memory/ - port + tools                                   │
│  └── ports.py - 25+ 个 Protocol / value object                │
└──────────────────────┬────────────────────────────────────────┘
                       │
              ┌────────┴────────┐
              │                 │
┌─────────────▼─────┐   ┌───────▼─────────────────────────────┐
│  LangChain 1.x    │   │  Sage Business Stores               │
│  create_agent     │   │  ├── SessionEventJournal (SQLite)   │
│  LangGraph 1.x    │   │  ├── TranscriptStore (SQLite)       │
│  AgentMiddleware  │   │  ├── CompactionStore (SQLite+HMAC)  │
│  checkpointer     │   │  ├── MemoryStore (revisioned)       │
└───────────────────┘   │  ├── KnowledgeStore (Wiki+RAG)      │
                        │  ├── RunStore (trace+diff+summary)  │
                        │  └── ArtifactStore (大输出归档)      │
                        └─────────────────────────────────────┘
```

## 一条请求怎么走

```text
用户在 /assistant 首页发消息
  ↓
POST /api/v1/coding/session + prompt  -> 创建 session（runtime_profile=legacy|deerflow_v2）
  ↓
WebSocket /api/v1/coding/{session_id}/stream
  ↓
api/coding.py::coding_stream
  ├── resolve_slash() - 解析 /skill 命令
  └── async for event in runtime.run_turn(content):
        ├── 每个事件先 SessionEventJournal.append (SQLite)
        ├── 再 broadcast 到 subscribers
        └── 再 websocket.send_json(event)
  ↓
CodingRuntime.run_turn (legacy) 或 HarnessRuntimeAdapter.run_turn (deerflow_v2)
  ↓
Engine.run_turn (legacy) 或 create_agent (deerflow_v2)
  ├── ContextManager.build / DurableContextMiddleware 投影
  ├── model.astream / create_agent stream
  ├── parse / 原生 tool calling
  └── ToolExecutor.execute / Middleware 链
  ↓
typed RunEvent -> trace.jsonl + timeline.sqlite3 + WebSocket
  ↓
前端 applyCodingEvent reducer -> 聊天/工具/审批/Diff/引用更新
```

这个流程里有三个关键拆分。

### 拆分 1：Runtime 和 Engine 分开

`CodingRuntime` 负责持有工作区、session、memory、tools、approval、plan_mode、worker_manager 等运行时对象图，建立 run 外层生命周期（lease、diff snapshot、trace、终态事件、finally 清理）。

`Engine` 负责把一个用户请求推进成模型调用、工具调用、最终回答。Engine 不知道 session、diff、WebSocket 的存在。

这样读代码时不用把对象装配和循环推进混在一起。`CodingRuntime.__init__` 是 composition root，`Engine.run_turn` 是 turn 循环。

### 拆分 2：工具执行和模型输出解析都拆出去

`core/coding/engine/model_output.py` 只处理 legacy 的 XML 协议，把模型返回解析成 `tool` / `tools` / `final` / `retry`。

`core/coding/tool_executor/executor.py` 只处理工具调用边界：参数校验、权限、policy、审批、执行、输出归档。

`deerflow_v2` runtime 不需要 XML parser，用 LangChain 原生 tool calling。但工具执行的权限/审批/policy 逻辑通过 `core/harness/tools_adapter.py` 复用。

### 拆分 3：事件有三个受众

一个 RunEvent 会被写到三个地方：

| 受众 | 存储 | 用途 |
| --- | --- | --- |
| 机器调试 | `trace.jsonl` | 完整字段，开发/benchmark 用 |
| 用户审计 | `timeline.sqlite3` | 用户可见事件，replay/刷新用 |
| 实时 UI | WebSocket | 前端增量更新 |

三个受众的字段不完全相同。`text_delta` 只推 WebSocket 不写 trace（避免 trace 膨胀）；`timeline.sqlite3` 的 payload 是完整事件 JSON；`trace.jsonl` 额外有调试元数据。

## 三层事实边界（最重要的设计约束）

**评审 Sage 时优先看这个表是否被违反。**

| 存储 | 事实源职责 | 不可做 | 持久化方式 |
| --- | --- | --- | --- |
| LangGraph checkpointer | Agent 执行状态、graph resume | 取代 Sage timeline 审计 | SQLite per session |
| SessionEventJournal | 用户可见事件、replay、UI 投影 | 保存业务对象完整内容 | `timeline.sqlite3` |
| TranscriptStore | 规范化消息查询、历史兼容 | 被压缩修改 | `transcript.sqlite3`（append-only） |
| KnowledgeStore | Wiki/Source/Revision/Citation | 被 Agent 直接写入（必须走 proposal） | PostgreSQL + 文件 |
| MemoryStore | 用户确认的稳定事实 | 接受模型自动持久化 | `state.json`（revisioned + atomic） |
| RunStore | run trace + diff + summary | 取代 timeline 终态 | `trace.jsonl` + `diff.json` |
| ArtifactStore | 长输出/文件/生成物 | 进入 model context 全文 | `tool-results/<call_id>.txt` |
| CompactionStore | 压缩 checkpoint + HMAC | 伪造摘要 | `compactions/<id>.json` |

**存储间通过稳定引用关联，不互相复制完整对象。** 例如 timeline 只存 `artifact_ref`，不存 artifact 内容；Knowledge page 只存 `source_revision`，不存 source 原文。

### 为什么不能合并

- checkpointer 是 LangGraph 内部状态，重启可恢复 graph；timeline 是用户可见审计，重启可重放事件。两者职责不同，合并会让 graph 内部状态污染审计。
- transcript 是 canonical（append-only，永不修改）；timeline 是 event log（可重放投影）。压缩只作用于 projection，transcript 永不动。
- KnowledgeStore 必须走 proposal，因为知识写入是长期事实，模型不能直接持久化（避免幻觉污染知识库）。
- MemoryStore 同理，`/remember` 是显式用户意图，`/dream` 只能生成 proposal。

## Runtime Profile 双轨

```text
┌───────────────────────┐     ┌────────────────────────┐
│  legacy               │     │  deerflow_v2            │
│  CodingRuntime + XML  │     │  sage_harness +         │
│  <tool>/<final>       │     │  LangGraph create_agent │
│  Engine               │     │  + 原生 tool calling    │
└───────────┬───────────┘     └───────────┬────────────┘
            │                             │
            └─────────┬───────────────────┘
                      │
              ┌───────▼───────┐
              │ session.runtime│
              │ _profile      │
              └───────────────┘
```

**规则**：
- session 创建时服务端选择并持久化
- run 开始后不可切换
- 历史 session 解释为 `legacy`，不静默迁移
- `deerflow_v2` 默认 gate 关闭，通过对等矩阵后才切

**为什么双轨不直接替换**：旧 session 的 history、trace、diff 都是 XML 协议格式，直接切会导致历史 replay 失败。双轨让新旧 runtime 共存，通过对等矩阵证明 v2 不劣于 legacy 后才切默认值。

## 一次 run 的核心状态机

```text
idle
  -> active_run_id = run_xxx
  -> snapshot_before_run
  -> turn_started
  -> engine events...
  -> workspace_diff_ready
  -> run_finished
  -> turn_finished
  -> active_run_id = None
```

V6 最关键的工程升级不是多了几个功能，而是终态变得明确：

- 同一个 session 同时只能有一个 active run（lease 互斥）
- Stop 可以携带 `run_id`，避免旧请求误停新 run
- 异常也要产生 `run_finished(status=error)`
- WebSocket 被关闭时，`finally` 仍要释放 run lease
- 前端等到 `run_finished` 才刷新 run history 和 diff 证据

## 为什么不是最小 ReAct

最小 agent 教学实现通常四块：context 管消息，LLM 管模型，tools 管工具，agent 管 ReAct 循环。这个拆法适合教学。

Sage 的问题更靠后一层。它关心的不只是 LLM 能不能调用工具，还包括：

- 任务跑了十几轮后，prompt 里到底保留哪些信息（Context 压缩）
- 工具调用失败、重复、越权、部分成功时，系统怎么记录和恢复（ToolExecutor + Policy）
- 子代理怎么有边界地执行，不把主 runtime 搞乱（受限 Research/Synthesize）
- 上下文压缩、checkpoint、durable memory 和 session history 怎么分工（三层事实边界）
- 一个版本到底有没有变好，靠什么 benchmark 和 trace 证明（evals/coding）
- 多用户在同一个服务器上怎么互不可见（云控制面 + Workspace ownership）
- 模型回答的引用怎么验证不是瞎编（Knowledge revision + content hash）

所以 Sage 的模块数量更多，原因是它开始处理最小 agent 之外的工程问题。

## 和 Pico v3 / Claude Code 的对标

| 维度 | Sage v7-beta | Pico v3 | Claude Code |
| --- | --- | --- | --- |
| 主循环 | legacy XML / `create_agent` | XML `<tool>/<final>` | SDK message + tool block |
| Prompt | ContextManager 三层 prefix + budget | ContextManager section + 预算 | system prompt parts + compact |
| 工具 | registry + Pydantic + deferred | Python registry 少量内置 | 大量独立工具 + MCP + LSP |
| 权限 | 五道门 + 11 危险模式 + Container Sandbox | tool profile + approval + write scope + sandbox | permission context + hooks |
| 记忆 | SQLite revisioned + proposal-only | file-based + auto-dream | CLAUDE.md + auto memory + team memory |
| 知识 | KnowledgeStore + PostgreSQL RAG + citation | 无 | 无（靠 workspace 文件） |
| 子代理 | 受限 Research/Synthesize/Practice + Evidence Bundle | WorkerManager + write_scope | Task tool |
| 状态 | session JSON + timeline SQLite + trace JSONL | session JSON + events JSONL + run artifacts | transcript + SDK messages + AppState |
| 部署 | Web + Docker Compose | 本地 CLI + TUI | 本地 CLI + Ink |

Sage 的优势是**有 Knowledge Platform 和持久 timeline**，Pico 的优势是**小而可读，模块边界容易讲清楚**，Claude Code 的优势是**产品级覆盖面和长期任务可靠性更完整**。

## Web 形态带来的边界

浏览器不能直接扫描用户电脑上的任意目录，也不能直接读取本地 Git 状态。当前 Sage 的 Git 和文件能力来自**服务端配置的 workspace checkout**。

```text
V7-beta：一个服务端 workspace，单用户受控私测
V7：每个受邀用户一个隔离的云 workspace + Git checkout
V8：Local Companion 连接用户本地未 push 的工作区
```

因此"网页里看到 Git 状态"不等于"网页直接读了本地电脑"。当前 `CodingRuntime.git_status()` 是服务器在 `workspace_root` 下运行 Git 命令。

## 第一入口

按顺序打开：

1. `core/coding/runtime.py::CodingRuntime.__init__` - legacy composition root
2. `core/coding/runtime.py::CodingRuntime.run_turn` - legacy run 生命周期
3. `core/harness/runtime_adapter.py::SageHarnessRuntimeAdapter` - v2 适配
4. `packages/sage_harness/sage_harness/agents/factory.py::create_sage_agent` - v2 agent 工厂
5. `core/coding/engine/engine.py::Engine.run_turn` - legacy turn 循环
6. `core/coding/tool_executor/executor.py::ToolExecutor.execute` - 工具执行管线

## 测试证据

- `tests/core/coding/test_runtime_run_lifecycle.py` - run 生命周期与 lease
- `tests/core/coding/test_agent_loop.py` - Engine 循环
- `tests/api/test_coding_routes.py` - API 契约
- `tests/harness/test_agent_factory.py` - v2 agent 工厂
- `frontend/src/stores/codingEvents.test.ts` - 前端事件归约

## 当前边界

> [!warning] v7-beta 的三层边界已经成型，但不是所有边界都已验证
> - `deerflow_v2` 默认 gate 关闭，主要跑 legacy runtime
> - 10 个 middleware 实现（设计目标 22 个），剩余 12 个在 Wave 6 补齐
> - Container Sandbox 未在目标服务器真实验证
> - 多用户隔离有 ownership 元数据，但 OS 级隔离未验证
> - 审批的 LangGraph durable interrupt 未实现（同进程 ApprovalManager 兜底）

## 自测

1. Sage 的三层（控制面/状态面/证据面）各自回答什么问题？
2. 为什么 Runtime 和 Engine 必须分开？如果合并会出什么问题？
3. 8 个存储的事实边界，为什么不能合并成一个"大数据库"？
4. legacy 和 deerflow_v2 双轨，为什么不能直接替换？
5. `final` 事件和 `run_finished` 事件有什么区别？为什么前端要等 `run_finished` 才刷新？

下一章：[[03-runtime-engine]]
