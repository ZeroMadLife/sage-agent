# Sage Learning Kickoff Dispatch Mini-Spec

> 日期：2026-08-25
>
> 状态：已实现，等待中枢三镜头再审
>
> 固定起点：`d545c9b9bf34038f7c5f63c3adfc3ea237d8a428`
>
> code candidate：`18af841ca05cbca7b31f78e1f9accad0656af9df`

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
- 并发连接继续由现有 `RunCoordinator` 内存锁与 Session Journal run lease 拒绝双启动。
- 普通 Coding 仍只处理原有 `UserMessage`；不新增普通 Coding kickoff 分支，也不改变 `startSessionWithPrompt`。

## 3. 恢复状态

- active Task 但 activation receipt GET 失败：前端进入显式 `receipt_recovery_failed`，展示错误和“重试恢复凭据”；不得显示无法工作的 active CTA。
- canonical Task/receipt 为 `activating`：POST 异常后的 catch 保留 `activating`，展示可刷新/轮询动作；不得覆盖成 `activation_failed`。
- activation active、kickoff receipt 缺失：确认动作 POST kickoff；响应丢失后先 GET canonical receipt，再用同 key 重复 POST。
- kickoff `dispatching`：客户端保持恢复态并用同 key重放 POST；只有 `accepted` 后才进入 Session。

## 4. Failure Points

后端 deterministic failure injection 至少覆盖：

1. `after_intent`：只保存 dispatch intent；重建 service/repository 后同 key POST 继续；
2. `after_journal`：Journal 已有稳定 `message_id`、Learning receipt 尚未 accepted；重启后重复 POST 不重复 Journal event 并收敛为 accepted；
3. `after_accepted`：accepted 已 durable、HTTP 响应可丢；canonical GET 与重复 POST 返回同一 receipt；
4. 两个 repository/service 实例并发同 key：只形成一个 accepted receipt 与一个 Journal user event；
5. 同 revision 不同 key、跨 revision/key 复用、非 active Task、activation/session binding 漂移：fail closed。

## 5. 验收

- backend API + repository/service tests 证明 revision/key binding、重启查询、failure replay、并发与一次 Journal acceptance；
- frontend tests 证明 accepted 前不进入 Session、丢响应后 canonical recovery、active receipt GET 失败可重试、canonical activating 不降级；
- 仓库内 Playwright spec 使用隔离 FastAPI/Vite 与非敏感 fake provider，覆盖创建、澄清、确认、失败重试、刷新恢复，并观测 accepted 前首轮为零、accepted 后首轮只有一次；
- 运行 Learning 邻接回归、完整前端、private/public build、Ruff、Mypy 与 `git diff --check`。

### 5.1 已执行证据

- Learning kickoff、Task/Activation 与 Coding 邻接后端：`69 passed`；
- Assistant API/store/view、Coding store/CodingView 与 Context Budget 聚焦前端：`5 files / 138 passed`；
- 完整 Vue：`69 files / 521 tests passed`；
- 仓库内 Playwright：`1 passed`，覆盖创建、三项澄清、activation 失败重试、kickoff dispatching、刷新恢复，以及 accepted 前 `turn_started=0`、accepted 后稳定 `turn_started=1`；
- 全仓 Ruff、Mypy（`270 source files`）、private/public production build 与 `git diff --check` 均通过；private build 仅有既有大 chunk warning。

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
- 不把 accepted receipt 描述成模型回答成功或完整运行恢复；运行中断继续遵守现有 Journal/Checkpoint 语义。

当前停止在本地 code candidate，未 push、未建 PR；等待中枢按需求、Runtime/恢复和前端兼容三个镜头再审。
