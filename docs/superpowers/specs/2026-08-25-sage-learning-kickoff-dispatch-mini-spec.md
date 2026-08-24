# Sage Learning Kickoff Dispatch Mini-Spec

> 日期：2026-08-25
>
> 状态：第二候选复审修复已实现，等待第三轮复审
>
> 固定起点：`d545c9b9bf34038f7c5f63c3adfc3ea237d8a428`
>
> code candidate：`21d3c07e9470462e92dce9eafc35252b2393b843`

## 1. 问题

L2 首个候选在 active activation receipt 与 Coding WebSocket 接受首轮消息之间只使用浏览器内存 `pendingInitialPrompts`。刷新、崩溃或进程重启会丢失待发送内容；发送后也没有 server-issued message identity 或 accepted receipt，无法证明重复进入不会重复首轮。

本修复不能使用 `localStorage` 伪造持久化，也不能改变普通 Coding WebSocket 的 `UserMessage` 合同。

## 2. 选定合同

### 2.1 服务端权威

- active `LearningTask`、`LearningActivationRecord`、Learning SQLite 和 Session Journal 共同构成权威状态；浏览器只投影 receipt。
- 客户端在取得 active activation receipt 后调用 `POST /api/v1/learning/tasks/{task_id}/kickoff`，body 只携带 `expected_revision`，header 携带 revision-bound `Idempotency-Key`。
- 服务端从 canonical active Task revision 读取首轮 topic，不接受浏览器重复提交 prompt；因此响应丢失后的重放不会产生内容漂移。
- `GET /api/v1/learning/tasks/{task_id}/kickoff` 查询 canonical receipt。重复 POST 使用同一 key 安全返回同一 receipt；同 revision 换 key 或同 key 跨 task/revision 使用均返回稳定冲突。

### 2.2 Receipt 与 Journal

内部 `LearningKickoffDispatchRecord` 固定：

- owner/workspace/task/revision；
- activation idempotency key digest 与 kickoff idempotency key digest；
- `dispatch_id`、`session_id`、`message_id`、`acceptance_run_id`、`turn_run_id`；
- canonical content hash；
- `receipt_status=dispatching|accepted`、`stage=intent|journal|accepted`；
- created/updated/accepted timestamps。

公开 response 不回显原始 idempotency key 或 topic，只返回浏览器进入共享 Session 所需的稳定身份、hash、状态与时间。

接受顺序：

```text
active activation receipt
  -> Learning SQLite: intent
  -> Session Journal: stable message_id user-accepted event
  -> Learning SQLite: accepted receipt
  -> client may enter/display shared Session
  -> Coding WS consumes accepted receipt and starts stable turn_run_id once
```

Session Journal 的 user-accepted event 使用独立 `acceptance_run_id`，不冒充模型运行的 `run_started`。同一 `message_id` 最多写入一次；若 SQLite 在 Journal 之后失败，重复 POST 必须识别并复用相同事件，再把 receipt 收敛到 accepted。

### 2.3 WebSocket 消费

- Coding WebSocket 建连后查询与该 active Session 绑定的 accepted kickoff receipt。
- 若 `turn_run_id` 尚无 Journal 事件，服务端用 canonical Task topic 启动一次共享 Harness run；该 run 不再追加第二条 user Timeline event。
- 若 `turn_run_id` 已存在任何 Journal 事件，连接只 replay，不重复启动。
- 服务端重启后，WS 直接重连会从持久 Session JSON 幂等 lazy rehydrate runtime；不要求浏览器先整页刷新或显式调用 REST resume。owner、workspace containment、model catalog、runtime profile 与 pending approval 校验继续复用原恢复合同。
- 并发 stale-check 后若另一个连接已完成同一固定 `turn_run_id`，后到连接在 `SessionEventJournalError`、`SessionThreadGoalConflictError` 或 active-run claim 失败后重新读取该 run；只有同一 run 已有 durable events 才收敛为 replay，其他真实冲突继续 fail closed。
- 普通 Coding 仍只处理原有 `UserMessage`；不新增普通 Coding kickoff 分支，也不改变 `startSessionWithPrompt`。

## 3. 恢复状态

- active Task 但 activation receipt GET 失败：前端进入显式 `receipt_recovery_failed`，展示错误和“重试恢复凭据”；不得显示无法工作的 active CTA。
- canonical Task/receipt 为 `activating`：POST 异常后的 catch 保留 `activating`，展示可刷新/轮询动作；不得覆盖成 `activation_failed`。
- activation active、kickoff receipt 缺失：确认动作 POST kickoff；响应丢失后先 GET canonical receipt，再用同 key 重复 POST。
- kickoff `dispatching`：客户端保持恢复态并用同 key 重放 POST；只有 `accepted` 后才进入 Session。
- kickoff receipt 仍存在但 canonical Task 行缺失：GET 与 WS 均返回稳定 `learning_kickoff_binding_conflict`，不得泄漏为 500。

## 4. Failure Points

后端 deterministic failure injection 至少覆盖：

1. `after_intent`：只保存 dispatch intent；重建 service/repository 后同 key POST 继续；
2. `after_journal`：Journal 已有稳定 `message_id`、Learning receipt 尚未 accepted；重启后重复 POST 不重复 Journal event 并收敛为 accepted；
3. `after_accepted`：accepted 已 durable、HTTP 响应可丢；canonical GET 与重复 POST 返回同一 receipt；
4. 两个 repository/service 实例并发同 key：只形成一个 accepted receipt 与一个 Journal user event；
5. 同 revision 不同 key、跨 revision/key 复用、非 active Task、activation/session binding 漂移：fail closed。
6. 新 app 直接连接原 Session WS：持久 runtime 被 lazy rehydrate，accepted kickoff 形成一次 start/terminal；普通 Coding 也可直接重连并继续发消息；
7. 两个 coordinator 同时通过空事件 stale-check：第一个完成固定 run 后，第二个只 replay；不同 active run 或真实 Thread Goal revision conflict 不得被吞掉；
8. 删除 canonical Task、保留 accepted receipt：kickoff GET 与 Coding WS 都稳定返回 binding conflict。

## 5. 验收

- backend API + repository/service tests 证明 revision/key binding、重启查询、failure replay、并发与一次 Journal acceptance；
- frontend tests 证明 accepted 前不进入 Session、丢响应后 canonical recovery、active receipt GET 失败可重试、canonical activating 不降级；
- 仓库内 Playwright spec 使用隔离 FastAPI/Vite 与非敏感 fake provider，覆盖创建、澄清、确认、失败重试、刷新恢复，并观测 accepted 前首轮为零、accepted 后首轮只有一次；
- 运行 Learning 邻接回归、完整前端、private/public build、Ruff、Mypy 与 `git diff --check`。

### 5.1 已执行证据

- restart/reconnect、并发、GET/WS 与完整 Coding Routes 定向：`73 passed`；
- 9 个 Learning API/core 邻接文件：`75 passed`；Cloud model、Coding surface、Thread Goal 与 Session Journal 邻接：`73 passed`；
- Assistant API/store/view、Coding store/CodingView 与 Context Budget 聚焦前端：`6 files / 138 passed`；
- 完整 Vue：`69 files / 521 tests passed`；
- 仓库内 Playwright：`1 passed`，覆盖创建、三项澄清、activation 失败重试、kickoff dispatching、刷新恢复，以及 accepted 前 `turn_started=0`、accepted 后稳定 `turn_started=1`；
- 全仓 Ruff/format（`482 files`）、pyproject 推荐 Mypy 范围（`249 source files`）、private/public production build 与 `git diff --check` 均通过；private build 仅有既有大 chunk warning。

关键后端与静态检查可复现命令：

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" \
  /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest \
  tests/api/test_learning_kickoff_dispatch.py tests/api/test_coding_routes.py -q
PYTHONPATH="$PWD/packages/sage_harness:$PWD" \
  /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest \
  tests/api/test_learning_kickoff_dispatch.py \
  tests/api/test_learning_task_activation.py tests/api/test_learning_task_routes.py \
  tests/api/test_learning_task_workspace_resume.py \
  tests/api/test_learning_timeline_projection.py \
  tests/core/learning/test_learning_activation_concurrency.py \
  tests/core/learning/test_learning_kickoff_dispatch.py \
  tests/core/learning/test_learning_task_bootstrap.py \
  tests/core/learning/test_learning_tasks.py -q
PYTHONPATH="$PWD/packages/sage_harness:$PWD" \
  /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest \
  tests/api/test_cloud_model_provider_routes.py \
  tests/api/test_coding_surface_context.py tests/api/test_coding_thread_goal.py \
  tests/core/coding/test_session_event_journal.py -q
/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m ruff check \
  api/ core/ db/ evals/ tests/
/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m ruff format --check \
  api/ core/ db/ evals/ tests/
/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m mypy \
  core/ api/ packages/sage_harness/
npm --prefix frontend run test -- --run \
  src/api/assistant.test.ts src/stores/assistantHome.test.ts \
  src/views/AssistantHomeView.test.ts src/stores/coding.test.ts \
  src/views/CodingView.test.ts src/components/coding/chat/CodingContextBudget.test.ts
npm --prefix frontend run test -- --run
npm --prefix frontend run build
npm --prefix frontend run build:public
```

Playwright 可复现命令：

```bash
SAGE_E2E_PYTHON=/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python \
  npm --prefix frontend run test:e2e
```

该 spec 启动隔离 FastAPI/Vite 与非敏感 fake provider；测试产物按仓库忽略规则写入 `output/playwright/`，不需要真实 Provider key。

## 6. 非目标与技术债

- 不实现 L3 Research、Artifact、Practice 或 Mastery；
- 不重写 `CodingView`，不为本修复抽取 Assistant 大表单；
- `AssistantHomeView` 表单/确认区拆分登记为 L3 前技术债；
- `input_origin + emit_user_event` 在 L3 前收敛为 `TurnInputKind/learning_kickoff` 类型合同，消除布尔组合表达输入来源的歧义；
- `LearningKickoffErrorCode` 与结构化 OpenAPI error responses 在 L3 前补齐；本轮只局部映射 Task 缺失，不扩展全部错误 schema；
- 不把 accepted receipt 描述成模型回答成功或完整运行恢复；运行中断继续遵守现有 Journal/Checkpoint 语义。

当前停止在本地 code candidate，未 push、未建 PR；等待中枢第三轮复审。
