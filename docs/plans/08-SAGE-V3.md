# Sage v3 落地记录

> 日期：2026-07-08
> 当前阶段：方向一完成；方向三完成；方向二完成；方向四完成；v3.x stop/cancel run 完成；v3.x approval UX 完成；v3.x run history 完成；v3.x session replay 完成
> 参考：`docs/superpowers/prompts/2026-07-08-codex-goal-sage-v3.md`

## 目标

Sage v3 开始向 Hermes / Hermes Web UI 的设计演进。本阶段先做三块后端地基：让 coding runtime 的 system prompt 在 session 生命周期内保持 byte-stable；再把工具系统从单文件表驱动改成装饰器注册 + 分模块实现；补上 ask 模式下的工具审批闭环。最后补齐一轮 Hermes Web UI 风格的工作台交互细节。

## 本阶段改动

### ContextManager prompt caching

`core/coding/context_manager.py` 新增：

- `build_system_prompt_once()`：同一 tools 集合下复用缓存的 system prompt。
- `invalidate_system_prompt()`：压缩、记忆刷新或后续工具/skill 变化时显式失效。
- 三层 prompt 结构：
  - stable：Sage 身份、工具指引、工具列表。
  - context：当前 workspace repository 上下文。
  - volatile：日期精度的 session date。
- `normalize_text()`：对 user/history/tool 文本做 `.strip()`，保持 prompt 前缀稳定。
- `system_prompt_build_count`：测试和调试用，确认缓存是否生效。

### Runtime 生命周期修正

`core/coding/runtime.py` 现在在 session 初始化时创建一个 `self.context_manager`，并在每轮 `Engine` 里复用它。这样缓存生命周期从“单轮”提升到“session”。

### Compaction 失效点

`core/coding/compact.py` 的 `compact()` 支持可选 `context_manager` 参数。发生真实压缩时会调用 `invalidate_system_prompt()`，为后续 memory/context 刷新留出接入点。

### 工具系统装饰器化

`core/coding/tools/registry.py` 现在只负责：

- `ToolDefinition`：保存工具 schema、schema model、风险、分类、handler 等元数据。
- `@register_tool(...)`：由工具模块在函数定义处注册。
- `registered_tool_definitions()`：返回已发现工具定义，便于测试和调试。
- `build_tool_registry()`：按 workspace/session 组装 `RegisteredTool`。
- `validate_tool()`：保留 pydantic 校验和 workspace 逃逸/文件存在性等安全约束。

工具实现拆分为：

- `file_tools.py`：`list_files` / `read_file` / `search` / `write_file` / `patch_file`
- `shell_tool.py`：`run_shell`
- `todo_tools.py`：`todo_add` / `todo_update` / `todo_list`
- `plan_tools.py`：`enter_plan_mode` / `exit_plan_mode`
- `agent_tools.py`：`agent` / `send_message` / `task_stop`

`RegisteredTool` 新增：

- `category`：`file` / `shell` / `todo` / `plan` / `agent`
- `requires_approval`：默认跟随 `risky`
- `timeout`：同步 runner 的通用超时保护，避免非 shell 工具长期卡住执行链路
- `PermissionChecker` 在 ask 模式下尊重 `requires_approval=False`，让工具可以显式声明“实现有写入/副作用风险，但已由更细的策略层治理，不需要再进入用户审批队列”。

### Approval MVP

新增 `core/coding/approval.py`：

- `ApprovalEntry`：保存 approval id、session、工具、参数、风险描述、危险模式 key 和阻塞事件。
- `ApprovalManager`：维护 session 队列，支持 `submit()` / `pending()` / `resolve()` / `is_session_approved()`。
- `check_dangerous_command()`：识别常见高风险 shell 模式，如 `rm -rf`、`git reset --hard`、`git push --force`、`sudo`、`curl | sh`、`docker compose down` 等。

后端运行链路：

- `CodingSessionRequest.approval_policy` 支持 `auto` / `ask` / `never`。
- `PermissionChecker` 在 `ask` 模式下对 risky 工具返回 `approval_required`。
- `Engine` 收到后发出 `approval_required` WebSocket 事件，并用 `asyncio.to_thread(entry.event.wait, 1.0)` 等待用户选择，避免阻塞 event loop。
- 新增 REST：
  - `GET /api/v1/coding/{session_id}/approval/pending`
  - `POST /api/v1/coding/{session_id}/approval/respond`

前端闭环：

- `CodingApprovalCard.vue`：在 composer 上方显示工具名、参数和风险说明。
- `stores/coding.ts`：监听 `approval_required` 事件；thinking 时轮询 pending；支持 Allow once / Deny。
- `api/coding.ts` / `types/api.ts`：补齐 approval 类型和请求函数。

### Hermes Web UI 交互增强

工具活动卡：

- 工具运行中默认展开；settled 时保持一帧再折叠，降低布局跳变。
- 工具结果超过 800 字符时智能截断，优先在句号、换行、分号处断开。
- 提供 Show more / Show less。
- diff 风格结果中 `+` / `-` 行做轻量高亮。

文件树和 git 状态：

- `stores/coding.ts` 增加目录缓存 `dirCache`、展开目录集合 `expandedDirs` 和代际号 `fileTreeGeneration`，避免重复请求和异步竞态覆盖。
- `tool_result` 事件中，`write_file` / `patch_file` / `run_shell` 成功后自动刷新文件树和 git badge。

Composer / Skills：

- context ring tooltip 展示 usage / budget / percent。
- context 使用率超过 75% 显示“压缩”提示按钮。
- Skills 面板支持搜索，并按 `builtin` / `user` / `project` 分类折叠。

### Stop / Cancel Run

补上 Hermes Web UI 里非常基础的运行控制能力：

- `CodingRuntime` 新增 session 级 `stop_requested` flag 和 `request_stop()`。
- `Engine` 支持 `should_stop` token，在模型调用前后、工具执行前后、tool loop 之间、approval 等待中检查 stop。
- stop 后 WebSocket 产生 `cancelled` 事件，内容为 `已停止当前运行。`。
- 如果 run 正卡在 approval pending，`ApprovalManager.cancel_session()` 会唤醒并 deny 当前 session 的 pending approval，避免后端挂住。
- 新增 REST：`POST /api/v1/coding/{session_id}/run/stop`。
- 前端 composer 在 thinking 时将 Send 切换为 Stop 按钮，调用 stop API；store 收到 `cancelled` 后把当前 assistant thinking message 收束为停止消息。

### Run History

把原本只落盘的 trace 提升成工作台可见能力：

- `RunStore` 增加 `list_runs()` 和 `get_run()`，从 `trace.jsonl` 生成 run summary 和 detail。
- run summary 包含 `status`、`event_count`、`tool_count`、`error_count`、`last_event_type`、`started_at`、`updated_at`。
- 新增 REST：
  - `GET /api/v1/coding/{session_id}/runs`
  - `GET /api/v1/coding/{session_id}/runs/{run_id}`
- 前端 store 增加 `runs` / `selectedRun`，初始化和 run 结束后刷新 run history。
- `CodingSidebar.vue` 增加 Runs 区块，显示状态、工具数、事件数、最后事件，并可展开最近 trace 事件。
- run detail 进一步增加 `timeline` 派生视图：后端把 `model_requested`、`tool_call`、`approval_required`、`tool_result`、`final` 等原始事件转成 `kind/title/detail/status/tool/timestamp`，前端侧栏优先渲染这份 worklog，避免把裸 JSON / 裸 event type 暴露给用户。

### Session History

补上 Hermes 工作台里最基础的会话可见性：

- `CodingRuntime` 创建时立即持久化初始 session，不再等到首轮 run 结束后才落盘。
- `CodingSessionStore` 增加 `list_sessions()`，从本地 `.coding/sessions/*.json` 生成 session summary。
- session summary 包含 `session_id`、`title`、`workspace_root`、`created_at`、`updated_at`、`runtime_mode`、`message_count`。
- 新增 REST：`GET /api/v1/coding/sessions`。
- 前端 store 增加 `codingSessions` / `loadSessions()`，初始化和 run 结束后刷新 session history。
- `CodingSidebar.vue` 增加 Sessions 区块，显示当前 session、标题、消息数和 runtime mode。
- `CodingRuntime.resume()` 可以从本地 session JSON 重建 runtime，恢复 history、todo ledger、plan mode、workspace 和工具上下文。
- 新增 REST：`POST /api/v1/coding/session/{session_id}/resume`。
- 前端 store 增加 `selectSession()`，点击 Sessions 区块中的历史会话后会调用 resume、清理当前消息/trace、刷新 workspace/run/session 状态并重连 WebSocket。
- resume 恢复的是可继续对话的 session 状态；运行中的 run 和 pending approval 不跨进程恢复。

### Session Message Replay

把 resume 从“恢复后端运行态”补齐到“恢复前端聊天上下文”：

- `CodingSessionStore.messages(session_id)` 从本地 session JSON 的 `history` 派生可重放消息。
- 只保留 `user` / `assistant` 两类消息，跳过 `tool` 等内部执行记录；工具轨迹仍由 Runs/worklog timeline 承载。
- 新增 REST：`GET /api/v1/coding/session/{session_id}/messages`。
- messages API 会校验持久化 `workspace_root` 必须在配置的 coding workspace 内，和 resume 的边界一致。
- 前端新增 `fetchCodingSessionMessages()`。
- `selectSession()` 在 resume 后拉取历史消息并写回中间聊天区，然后再刷新 workspace、run history、session history 并重连 WebSocket。
- 如果历史消息读取失败，前端退回空消息列表，但不阻断 session resume，避免单个损坏历史文件拖垮整个工作台。

## 测试覆盖

`tests/core/coding/test_context_compact.py` 新增：

- 同一 session 多轮 build 只构建一次 system prompt。
- invalidate 后下一轮重建。
- volatile tier 使用日期精度，不引入分钟/秒级 cache busting。
- compact 后能让 context cache 失效。

`tests/core/coding/test_tools.py` 新增：

- 工具发现后的公开工具集保持不变。
- 装饰器注册暴露 `ToolDefinition` 和 schema model。
- `category` / `requires_approval` 元数据正确。
- 同步 runner 有通用 timeout 保护。

`tests/core/coding/test_permissions.py` 新增：

- ask 模式下 `PermissionChecker` 尊重工具级 `requires_approval=False` 元数据。

Approval 相关新增：

- `tests/core/coding/test_approval.py`：submit/resolve/pending/session approval/危险命令检测。
- `tests/api/test_coding_routes.py`：pending/respond 端点；WebSocket 在 ask 模式下发 approval 并在 respond 后继续执行。
- `frontend/src/components/CodingApprovalCard.test.ts`：审批卡片渲染和按钮事件。
- `frontend/src/api/coding.test.ts` / `frontend/src/stores/coding.test.ts`：approval API 和状态流。

方向四新增：

- `frontend/src/components/CodingToolActivity.test.ts`：长工具结果截断、Show more、diff 行高亮。
- `frontend/src/stores/coding.test.ts`：目录缓存、工具写入后刷新文件树和 git 状态。

Stop / cancel 新增：

- `tests/core/coding/test_engine.py`：stop token 在模型调用前取消，并断言不会再调用模型。
- `tests/api/test_coding_routes.py`：`/run/stop` endpoint 会设置 runtime stop flag。
- `tests/core/coding/test_approval.py`：stop session 会唤醒 pending approval。
- `frontend/src/api/coding.test.ts` / `frontend/src/stores/coding.test.ts`：stop API、cancelled 事件和 stopCurrentRun 状态流。

Approval UX 新增：

- 前端 workbench 创建 coding session 时显式使用 `approval_policy=ask`，让审批能力成为真实工作台路径，而不是隐藏测试能力。
- `CodingApprovalCard.vue` 支持四种决策：Deny、Allow once、Allow session、Always。
- `write_file` / `patch_file` approval 增加 diff preview：
  - `patch_file` 直接用 `old_text` / `new_text` 生成预览。
  - `write_file` 先读取当前文件内容，再和待写入 content 生成预览；新文件或读取失败时按空文件处理。
- diff preview 支持打开完整 diff modal，用于在 Allow/Deny 前审阅完整改动，避免把高风险写入压缩在底部小卡片里。
- `frontend/src/components/CodingApprovalCard.test.ts` 覆盖四种 choice、diff 行高亮和完整 diff modal。
- `frontend/src/stores/coding.test.ts` 覆盖 write approval diff preview 生成。

Run history 新增：

- `tests/core/coding/test_run_store.py`：run summary 和 trace detail。
- `tests/api/test_coding_routes.py`：run history list/detail API。
- `frontend/src/api/coding.test.ts`：run history API client。
- `frontend/src/stores/coding.test.ts`：run list/detail 加载，以及 run finished 后刷新。
- `frontend/src/components/CodingSidebar.test.ts`：run detail 以可读 worklog timeline 渲染。

Session history 新增：

- `tests/core/coding/test_session_store.py`：session summary 派生与更新时间排序。
- `tests/core/coding/test_todo_plan_worker_runtime.py`：runtime resume 恢复 history、todo 和 plan mode。
- `tests/api/test_coding_routes.py`：coding sessions API 和 resume API。
- `frontend/src/api/coding.test.ts`：session history / resume API client。
- `frontend/src/stores/coding.test.ts`：session list 加载和 select session 状态切换。
- `frontend/src/components/CodingSidebar.test.ts`：Sessions 区块渲染、active 状态和点击切换。

Session replay 新增：

- `tests/core/coding/test_session_store.py`：session history 到可重放聊天消息的归一化，覆盖跳过 `tool` 角色。
- `tests/api/test_coding_routes.py`：messages API 返回持久化 user/assistant 历史。
- `frontend/src/api/coding.test.ts`：messages API client。
- `frontend/src/stores/coding.test.ts`：选择历史 session 后，中间聊天区重放历史消息而不是清空。

## 已验证

```bash
pytest tests/core/coding/test_context_compact.py -q
```

结果：`6 passed`

```bash
ruff check core/coding/context_manager.py core/coding/runtime.py core/coding/compact.py tests/core/coding/test_context_compact.py
mypy core/coding tests/core/coding/test_context_compact.py
```

结果：ruff 通过，mypy 通过。

```bash
pytest tests/core/coding/test_context_compact.py tests/core/coding/test_engine.py tests/api/test_coding_routes.py -q
```

结果：`22 passed`

```bash
pytest tests/core/coding/test_tools.py -q
```

结果：`9 passed`

```bash
mypy core/coding/tools tests/core/coding/test_tools.py
pytest tests/core/coding tests/api/test_coding_routes.py -q
```

结果：mypy 通过；coding/API 回归 `65 passed`

```bash
pytest tests/core/coding/test_approval.py tests/api/test_coding_routes.py tests/core/coding/test_permissions.py tests/core/coding/test_engine.py -q
cd frontend && npm run test -- --run
```

结果：后端 approval 定向 `28 passed`；前端 `13 files / 28 tests passed`

```bash
cd frontend && npm run test -- --run
cd frontend && npm run build
```

结果：前端 `14 files / 31 tests passed`；build 通过。

```bash
pytest tests/core/coding/test_engine.py tests/api/test_coding_routes.py tests/core/coding/test_approval.py -q
cd frontend && npm run test -- --run src/api/coding.test.ts src/stores/coding.test.ts
```

结果：后端 stop 定向 `24 passed`；前端 stop 定向 `2 files / 17 tests passed`

```bash
pytest tests/api/test_coding_routes.py tests/core/coding/test_approval.py -q
cd frontend && npm run test -- --run src/api/coding.test.ts src/components/CodingApprovalCard.test.ts src/stores/coding.test.ts
```

结果：后端 approval UX 定向 `22 passed`；前端 approval UX 定向 `3 files / 20 tests passed`

```bash
cd frontend && npm run test -- --run src/components/CodingApprovalCard.test.ts
cd frontend && npm run build
```

结果：approval diff modal 定向 `1 file / 3 tests passed`；前端 build 通过。

```bash
pytest tests/core/coding/test_run_store.py tests/api/test_coding_routes.py -q
cd frontend && npm run test -- --run src/api/coding.test.ts src/stores/coding.test.ts
```

结果：后端 run history 定向 `20 passed`；前端 run history 定向 `2 files / 21 tests passed`

```bash
pytest tests/core/coding/test_run_store.py tests/api/test_coding_routes.py::test_coding_run_history_lists_and_reads_traces -q
cd frontend && npm run test -- --run src/components/CodingSidebar.test.ts src/api/coding.test.ts src/stores/coding.test.ts
ruff check core/coding/run_store.py api/schemas.py tests/core/coding/test_run_store.py tests/api/test_coding_routes.py
mypy core/coding/run_store.py api/schemas.py tests/core/coding/test_run_store.py tests/api/test_coding_routes.py
cd frontend && npm run build
```

结果：后端 run timeline 定向 `4 passed`；前端 `3 files / 22 tests passed`；ruff/mypy/build 通过。

```bash
pytest tests/core/coding/test_permissions.py tests/core/coding/test_approval.py tests/core/coding/test_engine.py tests/api/test_coding_routes.py -q
ruff check core/coding/permissions.py tests/core/coding/test_permissions.py
mypy core/coding/permissions.py tests/core/coding/test_permissions.py
```

结果：tool permission metadata 定向 `34 passed`；ruff/mypy 通过。

```bash
pytest tests/core/coding/test_session_store.py tests/core/coding/test_todo_plan_worker_runtime.py tests/api/test_coding_routes.py -q
ruff check core/coding/session_store.py core/coding/runtime.py api/schemas.py api/coding.py tests/core/coding/test_session_store.py tests/api/test_coding_routes.py
mypy core/coding/session_store.py core/coding/runtime.py api/schemas.py api/coding.py tests/core/coding/test_session_store.py tests/api/test_coding_routes.py
cd frontend && npm run test -- --run src/api/coding.test.ts src/stores/coding.test.ts src/components/CodingSidebar.test.ts
```

结果：后端 session history 定向 `24 passed`；ruff/mypy 通过；前端定向 `3 files / 25 tests passed`。

```bash
pytest tests/core/coding/test_todo_plan_worker_runtime.py tests/api/test_coding_routes.py -q
ruff check core/coding/runtime.py api/coding.py tests/core/coding/test_todo_plan_worker_runtime.py tests/api/test_coding_routes.py
mypy core/coding/runtime.py api/coding.py tests/core/coding/test_todo_plan_worker_runtime.py tests/api/test_coding_routes.py
cd frontend && npm run test -- --run src/api/coding.test.ts src/stores/coding.test.ts src/components/CodingSidebar.test.ts
cd frontend && npm run build
```

结果：后端 session resume 定向 `25 passed`；ruff/mypy 通过；前端定向 `3 files / 28 tests passed`；前端 build 通过。

## 后续方向

1. Graphify 更新：完成 v3 主要方向后重新生成架构图谱。
2. 后续 v3.x：run detail 面板增强、session 消息回放、更细粒度 tool permission policy。
