# Codex Goal：移植 Pico 架构 → tour-agent 网页端 Coding 助手（第一版）

## 任务类型
goal 执行（自驱完成，直到验收通过）

---

## 背景与目标

在 `tour-agent` 仓库上，参照本地 Pico 项目（`/Users/zeromadlife/pico-agent/pico`）的 v3 runtime 架构，开发一个**网页端个人 Coding 助手**。本质是移植 Pico 的 engine/runtime/tools 核心到 tour-agent 现有的 FastAPI + Vue3 web 外壳里。

**核心交付**：用户在浏览器里输入 coding 任务（如"读一下 agents/react_agent.py 然后帮我加个 docstring"），agent 自主调工具（读文件/搜索/改文件/跑 shell），流式返回过程和结果。

**Pico 源码是参考架构，不是直接复制**——Pico 是 CLI/TUI，本次目标是 web。Pico 的 engine/runtime/tools 逻辑要移植，但 TUI 外壳丢弃，改用 tour-agent 现有的 WebSocket 流式接口。

---

## 两条红线

1. **旅游侧代码保留不动**：`agents/graph.py`、`agents/itinerary_tool.py`、`mcp_servers/amap|weather|scenic`、`core/verifier.py`、`evals/` 等全部保留，本次不删不改不迁移。新的 coding 能力是**新增**，不是替换。
2. **现有测试不破**：`bash scripts/check.sh` 必须全绿。新增 coding 模块要有自己的测试。

---

## Pico 架构映射（必读）

先读 Pico 源码理解架构，再动手。关键文件：

| Pico 文件 | 职责 | 本次移植到 |
|-----------|------|-----------|
| `pico/core/runtime.py` | Runtime 状态：session/workspace/memory/tools/checkpoint 组合 | `core/coding/runtime.py`（新增）|
| `pico/core/engine.py` | Turn 控制循环：model→parse→tool→final 的 generator | `core/coding/engine.py`（新增）|
| `pico/core/engine_helpers.py` | 工具执行、memory 维护、step limit 处理 | `core/coding/engine_helpers.py`（新增）|
| `pico/core/model_output.py` | 解析模型输出的 `<tool>`/`<final>` 标签协议 | `core/coding/model_output.py`（新增）|
| `pico/core/context_manager.py` | prompt 组装 + 上下文预算（section 分配） | `core/coding/context_manager.py`（新增）|
| `pico/core/compact.py` | 会话压缩（旧历史折叠为摘要） | `core/coding/compact.py`（新增）|
| `pico/core/workspace.py` | 工作目录上下文、路径安全、clip | `core/coding/workspace.py`（新增）|
| `pico/core/permissions.py` | 工具权限准入（read_only/approval/write_scope） | `core/coding/permissions.py`（新增）|
| `pico/core/tool_policy.py` | 工具策略（patch 前需 read、shell 搜索拦截） | `core/coding/tool_policy.py`（新增）|
| `pico/tools/registry.py` | 6 个核心工具 + 注册表 | `core/coding/tools/registry.py`（新增）|
| `pico/tools/base.py` | RegisteredTool / ToolResult | `core/coding/tools/base.py`（新增）|
| `pico/tools/schemas.py` | 工具参数 pydantic 模型 | `core/coding/tools/schemas.py`（新增）|
| `pico/core/todo_ledger.py` | Todo 任务追踪 | `core/coding/todo_ledger.py`（新增）|
| `pico/core/plan_mode.py` | Plan mode（先规划再执行） | `core/coding/plan_mode.py`（新增）|
| `pico/core/worker_manager.py` | Worker 子 agent 生命周期 | `core/coding/worker_manager.py`（新增）|
| `pico/core/session_store.py` / `session_events.py` / `run_store.py` | 会话持久化 + 事件流 + run trace | `core/coding/session_*.py` / `run_store.py`（新增）|

**所有新代码放在 `core/coding/` 下，与旅游侧 `agents/`、`core/memory/` 等完全隔离。** 这样旅游侧零影响。

---

## 分层实施（按顺序，每层绿了再下一层）

### Layer 0：工作目录与路径安全（地基）

新建 `core/coding/workspace.py`，移植 Pico 的 `WorkspaceContext`：

- `root`：工作目录（默认 tour-agent 仓库根，或用户指定）
- `path(relative)`：把相对路径解析为安全绝对路径，禁止逃逸 root（防 `../../etc/passwd`）
- `clip(text, limit)`：截断长输出
- `now()`：ISO 时间戳
- `IGNORED_PATH_NAMES`：`.git`/`__pycache__`/`.venv` 等

**验收**：路径逃逸测试通过（`../../` 被拒），正常路径解析正确。

### Layer 1：6 个核心工具 + 注册表

新建 `core/coding/tools/`，移植 Pico 的 `registry.py` + `base.py` + `schemas.py`：

6 个工具（与 Pico 完全一致）：

| 工具 | 参数 | 风险 | 说明 |
|------|------|------|------|
| `list_files` | `path='.'` | 只读 | 列目录，标 `[D]`/`[F]` |
| `read_file` | `path, start=1, end=200` | 只读 | 按行范围读，带行号 |
| `search` | `pattern, path='.'` | 只读 | 优先 ripgrep，无则纯 Python fallback |
| `run_shell` | `command, timeout=20` | **风险** | 执行 shell，带超时 + 工作目录约束 |
| `write_file` | `path, content` | **风险** | 写文件 |
| `patch_file` | `path, old_text, new_text` | **风险** | 精确替换（old_text 必须唯一命中一次） |

关键设计（从 Pico 继承）：
- `RegisteredTool`：name/schema/description/risky/runner
- `ToolResult`：content + is_error
- `validate_tool`：参数校验 + workspace 路径安全检查
- `patch_file` 的 `old_text` 必须在文件里**恰好出现一次**，否则拒绝
- `run_shell` 传 `env` 时用 allowlist 过滤（不继承父进程全部环境变量）

**验收**：6 个工具单测通过，包括路径安全、patch 唯一性、shell 超时。

### Layer 2：工具治理（permission + tool_policy）

新建 `core/coding/permissions.py` 和 `core/coding/tool_policy.py`，移植 Pico 的两层治理：

**PermissionChecker**（权限层）：
- 只读工具直接 allow
- 写操作按 approval_policy（`auto`/`ask`/`never`）决策
- write_scope 限制（worker 子 agent 只能写指定目录）
- plan mode 下只允许只读工具

**ToolPolicyChecker**（策略层，在 permission 之上）：
- `patch_file` / `write_file`（覆盖已存在文件）前必须最近 read 过该文件（freshness 检查，防盲改）
- `run_shell` 拦截 `cat|grep|find|ls` 等应该用 read_file/search 完成的事（只在命令起始位置拦截，管道后允许）

**验收**：没读就 patch 被拒、plan mode 下 write 被拒、shell 搜索拦截生效。

### Layer 3：模型输出协议 + Engine 循环

这是核心。新建 `core/coding/model_output.py` + `core/coding/engine.py` + `core/coding/engine_helpers.py`。

**模型输出协议**（移植 Pico 的 XML 标签协议）：

模型输出两种标签之一：
- `<tool name="read_file" path="..."></tool>` 或 `<tool>{"name":"read_file","args":{...}}</tool>` → 调工具
- `<final>最终回复</final>` → 结束本轮

`model_output.parse(raw)` 返回 `("tool", payload)` / `("final", text)` / `("retry", notice)`。

**Engine 循环**（移植 Pico 的 generator 模式）：

```python
class Engine:
    def run_turn(self, user_message):
        # 每轮：组装 context → 调模型 → 解析输出 → 执行工具/返回 final
        # yield 事件：tool_call / tool_result / final / step_limit / error
        # 最多 max_steps 轮（默认 50），超了产出三段式总结
```

关键点：
- engine 是 generator，yield 事件——这天然适配 WebSocket 流式
- 每轮先组装 context（prefix + memory + history + current_request），控制总 token 预算
- 模型输出被 `model_output.parse` 解析，是 tool 就执行工具并把结果追加到 history，是 final 就结束
- 工具执行前后发 `tool_started`/`tool_finished` 事件
- step 超限时产出三段式总结：已完成/未完成/如何继续

**LLM 接入**：复用 tour-agent 现有的 `core/llm.py`（DeepSeek via ChatOpenAI）。不移植 Pico 的 provider profile 系统。Engine 调 `core.llm` 获取模型客户端，按 Pico 的 `complete_model` 契约包装。

**验收**：给一个简单任务（"读 README.md 告诉我项目叫什么"），engine 能跑完 model→tool→final 循环，yield 正确事件序列。

### Layer 4：Context 组装 + 压缩

新建 `core/coding/context_manager.py` + `core/coding/compact.py`，移植 Pico 的上下文预算控制：

**ContextManager**：
- 总预算 60000 token，分 section：prefix/memory/skills/relevant_memory/history/current_request
- 每个 section 有 budget 和 floor
- 超预算时按 reduction_order 压缩（先压 relevant_memory，再 skills...）
- 输出最终 prompt 文本

**CompactManager**：
- 历史太长时，把旧 turn 折叠成一条 `compact_summary`
- 保留最近 N 轮原始历史
- 触发条件：token 超预算 / 手动 `/compact`

**验收**：长对话（模拟 30 轮）后 context 不超预算，压缩后 history 含 compact_summary 条目。

### Layer 5：Todo 任务追踪

新建 `core/coding/todo_ledger.py`，移植 Pico 的 todo 工具：

3 个工具：`todo_add` / `todo_update` / `todo_list`

- 模型可以建任务、更新状态（pending/in_progress/completed）、列任务
- todo 状态存在 session 里
- web 前端可展示任务列表（后续 Layer 8 做）

**验收**：todo_add 后 todo_list 能返回，todo_update 能改状态。

### Layer 6：Plan Mode

新建 `core/coding/plan_mode.py`，移植 Pico 的 plan mode：

2 个工具：`enter_plan_mode` / `exit_plan_mode`

- `enter_plan_mode(topic)` → 切到 plan 模式，只允许只读工具 + Explore worker
- plan 写到一个 artifact 文件（`.pico/` 或 tour-agent 下 `.coding/plans/`）
- `exit_plan_mode()` → 切回 default，恢复全部工具

**验收**：plan mode 下 write_file/run_shell 被拒，exit 后恢复。

### Layer 7：Worker 子 agent

新建 `core/coding/worker_manager.py` + `worker_execution.py` + `worker_runtime.py`，移植 Pico 的 coordinator-worker：

3 个工具：`agent` / `send_message` / `task_stop`

- `agent(prompt, subagent_type, write_scope)` → spawn 子 agent 跑独立任务
- 子 agent 有自己的 task_id 和受限 write_scope
- `send_message(task_id, message)` → 给子 agent 发消息继续
- `task_stop(task_id)` → 停止子 agent
- 子 agent 完成后通过 notification 通知主 agent

**Pico 用 threading.Thread，web 端要注意**：考虑用 `asyncio.create_task` 或保持 thread + run_in_executor。子 agent 共享同一套 tools/engine，但 session 和 write_scope 隔离。

**验收**：spawn 一个 worker 让它 read_file，worker 完成后主 agent 收到 notification。

### Layer 8：Runtime 组装 + Session 持久化

新建 `core/coding/runtime.py` + `session_store.py` + `session_events.py` + `run_store.py`，把以上所有组件组装成 Runtime：

```python
class CodingRuntime:
    """一个完整的 coding agent session 状态。"""
    # 组合：workspace / tools / engine / context_manager / compact
    #       / permissions / tool_policy / todo_ledger / plan_mode
    #       / worker_manager / session_store / session_event_bus / run_store
    #       / memory（可复用 tour-agent 现有 core/memory，或简化版）
```

- session 持久化到本地（`.coding/sessions/<id>.json` + `.events.jsonl`）
- run trace 持久化（`.coding/runs/<run_id>/trace.jsonl` + `task_state.json` + `report.json`）
- SessionEventBus：写事件流，供前端消费

**验收**：一次完整 turn 后，session JSON 和 events.jsonl 文件存在且内容正确。

### Layer 9：API 接入 + WebSocket 流式

改造 `api/` 层，新增 coding 路由（与现有旅游 chat 路由并行）：

- `POST /api/v1/coding/session` → 创建 coding session（指定工作目录）
- `WS /api/v1/coding/{session_id}/stream` → 长连接，收消息、流式返回 engine 事件
- 事件格式适配前端：`tool_call` / `tool_result` / `final` / `step_limit` / `error` / `todo_update` / `plan_update` / `worker_notification`

**关键**：engine 的 generator yield 的事件，转成 WebSocket message 推给前端。参考现有 `api/services/chat_runner.py` 的适配模式。

**验收**：wscat 连上 WebSocket，发"读 README.md"，收到 tool_call → tool_result → final 事件流。

### Layer 10：前端最小可用

在 `frontend/` 加一个 Coding 视图（与现有 ChatView 并行）：

- 消息列表（用户消息 + agent final 回复）
- 工具调用过程展示（tool 名 + 参数 + 结果摘要，折叠式）
- 输入框
- **不追求美观**，只要能跑通端到端。Markdown 渲染可复用现有组件。

**验收**：浏览器里输入"读 README.md 告诉我项目叫什么"，能看到工具调用过程 + 最终回复。

---

## 不要做的事

- **不要动旅游侧代码**（agents/、mcp_servers/、core/verifier.py、evals/、core/memory/ 里旅游相关部分）
- **不要移植 Pico 的 TUI**（`pico/tui/`）——目标是 web
- **不要移植 Pico 的 CLI / commands**（`pico/cli.py`、`pico/commands/`）
- **不要移植 Pico 的 provider profile 系统**——复用 tour-agent 现有 `core/llm.py`
- **不要移植 Pico 的 Skill 系统**（`pico/features/skills*.py`）——后续再做
- **不要移植 Pico 的 sandbox/bubblewrap**——macOS 不支持，web 端用 approval + 工作目录约束即可
- **不要做 RAG / retrieve 工具**——后续单独做
- **不要做外部 agent 接入**（Dify/Coze）——已砍掉
- **不要改前端现有 ChatView**——新增 Coding 视图

---

## 执行顺序建议

```
Layer 0  workspace          ← 地基，先做
Layer 1  tools (6个)        ← 工具是核心
Layer 2  permission+policy  ← 工具治理
Layer 3  model_output+engine ← 核心循环（最难，重点）
Layer 4  context+compact    ← 上下文控制
Layer 5  todo               ← 简单，可穿插
Layer 6  plan_mode          ← 依赖 Layer 2
Layer 7  worker             ← 依赖 Layer 1-3
Layer 8  runtime组装+持久化  ← 把前面组装起来
Layer 9  API+WebSocket      ← 对外暴露
Layer 10 前端最小可用        ← 端到端验证
```

**每完成一层，跑 `bash scripts/check.sh` 确认没破现有测试，再跑新模块的测试。**

---

## 验收标准（全部满足才算完成）

1. `bash scripts/check.sh` 全绿，旅游侧测试零回归
2. `core/coding/` 下所有模块存在，各有单测
3. 6 个核心工具可调用，路径安全 + patch 唯一性 + shell 超时生效
4. permission + tool_policy 两层治理工作（盲改被拒、plan mode 限制、shell 搜索拦截）
5. engine 能跑完 model→tool→final 循环，yield 正确事件
6. context 预算控制工作，长对话能压缩
7. todo / plan_mode / worker 三套能力各自可用
8. session + events + run trace 持久化到本地文件
9. WebSocket 端到端：wscat 发消息能收到流式事件
10. 前端 Coding 视图能跑通"读文件→回答"端到端

---

## 参考文档（在 Pico 仓库里）

- `pico/release/v3/REVIEW.md` — 架构总览
- `pico/release/v3/CHANGELOG.md` — v3 能力清单
- `pico/docs/memory.md` — 分层记忆设计（本次简化版即可）
- `pico/docs/skills.md` — Skill 系统（本次不做，仅供后续参考）
- `pico/docs/sandbox.md` — 沙盒（本次不做）
- `pico/docs/configuration.md` — provider 配置（本次复用 tour-agent 现有）

---

## 完成标志

`bash scripts/check.sh` 全绿 + 前端端到端跑通 + 所有 Layer 验收通过。

完成后：
1. commit message 标注 `coding-agent-v1`
2. 在 `docs/plans/` 下新建 `06-CODING-AGENT-V1.md` 记录实际改动、各 Layer 完成情况、验收结果
3. 更新 `README.md` 加一段"Coding 助手"说明（与旅游助手并列）
