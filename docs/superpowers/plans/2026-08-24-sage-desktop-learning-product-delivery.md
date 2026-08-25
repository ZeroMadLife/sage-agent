# Sage 桌面端长期学习产品实施计划

> 状态：已获 CTO 全权实施授权，按阶段本地开发与审查；公开 push、PR 合并、签名凭据和发布仍按外部变更门禁单独执行。
>
> L2 状态（2026-08-25）：Assistant code candidate `2ae52dfc47080a5349f2b3bbc00e9f182ecc1b8b` 与最终复审通过的 docs candidate `ca6e618d6df993dff40ed3a304ca942c239e7d84` 共同构成 L3 固定起点；仅本地 commit，未 push、未建 PR、未合入。
>
> L3 状态（2026-08-25）：前两轮候选均未通过中枢三镜头复审；L3.1 code candidate `dd9c93f` 已在原职责分支完成，等待第八轮三镜头复审；未 push、未建 PR、未合入 `dev/sage-v7`。
>
> 设计来源：`docs/superpowers/specs/2026-08-24-sage-desktop-learning-product-design.md`
>
> 设计提交：`b2891b1127e5b7b3038c0758eb611faf4e1bec17`

## 1. 交付策略

按纵向能力切片，不按“先后端、再前端、最后测试”横向推进。第一阶段先证明两个最高风险路径：

1. Python/FastAPI/Harness 能否作为 macOS arm64 sidecar 在无 Python/Node 的机器上启动；
2. 已有可恢复学习任务 A1/A2 能否安全接回当前 `dev/sage-v7`，并与新设计的三种 plan identity 兼容。

所有功能在独立 worktree 和职责分支完成，先定向验证、再代码审查、再进入集成分支。根 worktree 只做同步和集成验证。

## 2. 当前可复用资产

| 资产 | 当前状态 | 处理方式 |
| --- | --- | --- |
| 产品设计 | `docs/desktop-learning-product-design@b2891b1` | 作为所有切片的事实来源 |
| 认证产品化 | `feat/productization-foundation`，23 个文件未提交 | 先独立审查与 checkpoint，不重写 |
| 可恢复学习 A1/A2 | `feat/recoverable-learning-bootstrap-v1@20182e4` | 已提交候选；审查后迁移到新基线 |
| 旧学习计划 | `docs/recoverable-learning-task-v1@5054381` | 复用已验证合同，按新 plan identity 修订 |
| Harness/RAG/Memory/Mastery | 当前 `dev/sage-v7@87478e8` | 作为底层原语，不宣称已完成产品接线 |

已复验：认证候选 `49` 个后端/脚本测试、`7` 个前端认证测试和生产构建通过；学习 A1/A2 候选 `27` 个测试及 Ruff 通过。

## 3. 依赖图

```text
P0 认证候选审查与 checkpoint ───────────────┐
                                              ├─ D2 首次启动/Keychain/Provider
D0 macOS sidecar packaging spike ── D1 安全桌面宿主 ┤
                                              └─ D3 Cloud OAuth/会话

L0 学习 A1/A2 候选审查与基线迁移
  └─ L1 只读 Learning Scope
      └─ L2 Assistant 任务确认
          └─ L3 Research/Synthesize/Learning Artifact
              └─ L4 Practice/Mastery/Resume
                  ├─ M0 Memory 控制面
                  └─ E0 学习与记忆 Eval

D1 + D2 + L2/L3/L4 ── I0 桌面端首次学习 E2E ── R0 发布候选收口
```

P0、D0、L0 可并行。M0/E0 后置，不阻塞用户先使用桌面学习闭环。

## 4. 切片 P0：认证产品化候选收口

**交付行为**

- 保留 Web HttpOnly Cookie；为桌面/TUI 提供短期 access token、opaque refresh rotation、设备撤销和统一 owner 校验；
- 保留 `quickstart.sh` 作为开发/源码启动入口，不把它包装成桌面安装器；
- 将当前未提交成果整理为职责清晰的本地 checkpoint commit。

**公共 seam**

- `/api/v1/cloud/auth/device/login`；
- `/api/v1/cloud/auth/token/refresh`；
- `/api/v1/cloud/auth/token/revoke`；
- HTTP/WS Cookie + Bearer 统一依赖；
- `scripts/quickstart.sh --check/--start`。

**验收证据**

- access token 过期、session 撤销、refresh replay 和设备 family 撤销均 fail closed；
- JWT 不授予 workspace 权限，跨 owner 请求仍由资源查询拒绝；
- refresh token 不进入 URL、日志或 localStorage；
- 定向测试、Ruff、Mypy、前端认证测试与生产构建通过。

**非目标**

- 不在 P0 实现 Keychain、桌面 OAuth、公开注册或计费。

## 5. 切片 D0：macOS sidecar packaging spike

**交付行为**

- 选择 PyInstaller one-dir，打包一个与 Sage API 同 Python 3.12 运行时的 sidecar；
- 在临时数据目录启动最小 FastAPI app，验证 liveness/readiness、SQLite checkpoint、TLS Provider client 和优雅退出；
- 生成版本、build SHA、依赖清单和可重复构建 receipt。

**公共 seam**

- `desktop/sidecar/` 构建入口；
- `sage-api-aarch64-apple-darwin` external binary；
- `/health/live`、`/health/ready` 的最小 schema；
- sidecar `--bind 127.0.0.1 --port 0 --data-dir ...` 启动合同。

**验收证据**

- 无仓库 `.venv`、无系统 Python/Node 的隔离环境能够启动产物；
- 核心 import、SQLite 读写、checkpoint 重开、HTTPS 请求初始化通过；
- 端口为 OS 分配，进程退出后无遗留 child；
- 产物不包含 `.env`、Provider key、用户数据或绝对开发路径。

**依赖与非目标**

- 无功能依赖，可与 P0/L0 并行；
- spike 不承诺 DMG、Keychain、OAuth、updater 或完整前端。

## 6. 切片 D1：安全桌面宿主

**交付行为**

- Tauri 2 启动 Vue build 和 D0 sidecar；
- sidecar 通过匿名 pipe 上报身份，Rust 校验后向 Vue 提供 endpoint；
- HTTP/SSE/WS 使用启动期 bearer、Host/Origin 校验；
- 展示 ready/degraded/blocked 能力页，支持退出、崩溃退避和熔断。

**公共 seam**

- Desktop Host Adapter；
- `{pid, port, instance_id, api_version, build_sha, nonce}` handshake；
- `/health/live`、`/health/ready`、`/capabilities`；
- single-instance lock、orphan cleanup、crash budget。

**验收证据**

- 端口抢占、本机伪造 handshake、错误 nonce、旧 API version 和跨 Origin 请求被拒绝；
- 窗口隐藏、连接断开、sidecar 崩溃、显式退出和机器重启符合恢复矩阵；
- Tauri 不开放通用 shell、任意远程导航、devtools 或跨 workspace 文件访问；
- macOS arm64 本地开发包 smoke 通过。

**依赖与非目标**

- 依赖 D0；
- Developer ID/notarization 作为 D1 发行子门禁，缺少签名凭据时不阻塞开发包验证，但不能宣称可公开安装。

## 7. 切片 D2：首次启动、Keychain 与 Local Provider

**交付行为**

- 首次启动选择 Local/Cloud，完成数据目录、迁移、workspace 和 capability 检查；
- Rust 持有 macOS Keychain 所有权，SQLite 只存 `key_ref/key_hint`；
- Local Provider 支持新增、探测、选择默认模型、轮换和删除；
- 无 Docker 时仍可对话/RAG，副作用工具明确 blocked。

**公共 seam**

- Desktop Secret Broker；
- Provider metadata/key-ref DTO；
- `ready/degraded/blocked + reason_code + action`；
- onboarding state machine。

**验收证据**

- Keychain 锁定、拒绝访问、轮换、注销和删除都有可恢复错误；
- secret 不进入环境变量、SQLite、Timeline、日志或诊断包；
- 无 Docker/PostgreSQL/Web Search 的降级矩阵与设计一致；
- 首次启动、刷新和重启后 onboarding 状态可重建。

**依赖与非目标**

- 依赖 P0、D1；
- 不实现 Cloud OAuth 和 updater。

## 8. 切片 D3：Cloud OAuth 与桌面会话

**交付行为**

- 系统浏览器 + PKCE + 一次性 state + Rust 临时 loopback callback；
- Rust 交换并保存桌面 refresh/session credential，Vue 不接触 token；
- sidecar 作为受限 broker 调用 Cloud API，支持撤销和重新登录。

**验收证据**

- state 过期、callback replay、错误 redirect、授权码重放和已撤销设备全部拒绝；
- Cloud Provider key 只留服务器；
- WebView 不依赖系统浏览器 Cookie 共享；
- 登录、刷新、退出、重启恢复和跨 owner 拒绝 E2E 通过。

**依赖与非目标**

- 依赖 P0、D1；可以与 D2 后半段并行；
- 首版不同时实现 deep-link OAuth。

## 9. 切片 L0：可恢复学习 A1/A2 迁移

**交付行为**

- 审查并迁移现有 draft、CAS 修改、activation intent、幂等 Session/Goal/TurnContextPlan bootstrap 和 reconciliation；
- 使用 expand-migrate-contract：内部和新持久化以 `turn_context_plan_id/turn_context_plan_hash` 为权威；公共 API 在 expand 阶段保留 deprecated `plan_id/plan_hash` 投影，持久化 decoder 兼容读取同名旧字段；
- L0 不生成 LearningPlan 或 Task DAG，`learning_plan_id/learning_plan_hash/dag_hash` 保持为空，分别留到 L3/L4 的真实合同生成；
- Task、activation、receipt 与全部查询以 `owner_id + workspace_id` 为 canonical scope；workspace 由服务端派生；
- receipt v3 固化 task revision、catalog/capability revision 和覆盖 knowledge/web/domains/freshness 的 source policy snapshot/revision；
- 新 receipt 使用 `resume_validation_version=canonical_l0_v3` 严格校验全部新字段；真实 v2 active receipt 迁移为 `legacy_l0_v2`，只校验旧 TurnContextPlan 当时实际保存的等价字段，不伪造其曾冻结后来新增的 resume/source revision；
- `/resume` 在 L0 只重验证 canonical Session 与 TurnContextPlan，不宣称已实现运行中 Checkpoint Resume；legacy active 只在资源能唯一证明 workspace 时回填，其他旧行不可见并 blocked；
- Session 失败补偿由 activation 状态 CAS/fencing 授权，active commit 在同一 SQLite 写事务内重新确认 Session 可见，防止并发失败 writer 留下 archived 的成功 Session。

**公共 seam**

- `POST/GET/PATCH /api/v1/learning/tasks`；
- `POST /api/v1/learning/tasks/{task_id}/activate`；
- activation receipt/reconciliation；
- Learning Task repository。

**验收证据**

- 两个独立 repository/service/resources 实例共享 SQLite/storage 时，同 key 只产生一个 intent/Session/Goal/Plan，不同 key 只有一个 winner，stage 不回退；四个故障注入点重启后完成或补偿；
- 孤立 Session 被归档；同 stage 竞争时，失败 writer 先补偿也不能让最终 active Session 保持 archived，active 后的旧归档快照不能执行回调；
- `learning_plan_hash`、`turn_context_plan_hash`、`dag_hash` 不再混用，旧 `plan_hash` 只映射到 TurnContextPlan；
- Session/Plan 缺失、删除、篡改，以及 owner/workspace/task/catalog/capability/source policy 漂移均稳定 `409`；LearningTask 与 receipt 任一侧出现非空 `learning_plan_id/learning_plan_hash/dag_hash` 都 fail closed；
- 真实 a6d v2 active receipt 加旧 TurnContextPlan fixture 能完成 GET 与 legacy 等价 Resume；缺失旧时代已有的 source/tool 字段仍 fail closed；
- 定向测试、相邻恢复回归、Ruff、Mypy 和前端 private/public production build 通过后才可收口。

**依赖与非目标**

- 基于现有 `feat/recoverable-learning-bootstrap-v1@20182e4`，不重新实现；
- 本片不执行检索、生成学习 Artifact 或写 Mastery。

## 10. 切片 L1：首轮只读 Learning Scope

> 候选状态（2026-08-24）：Runtime fix code candidate
> `9a454d24843dd27f2e2c00bb34366219c428675e` 已补上最终 Runtime 复审指出的 no-runtime
> HTTP Timeline Session 缺失/损坏 P2，并完成测试 helper 所有权整理；当前仍待中枢最后 Runtime 短复审，
> 尚未合入 `dev/sage-v7`。实现复用 L0 canonical resume
> validation、Capability Registry、ToolBundle 和 Harness middleware：模型 catalog 与真实
> ToolNode 调用均受 `AllowedCapabilitySet + capability_revision + turn_context_plan_hash`
> 约束；host Memory retrieval 与 Research child 的每次 model/tool 边界也会 canonical 重验。
> `knowledge=disabled` 可按冻结策略直接开放只读 Web；`web=allowed_when_insufficient` 在 L1 尚无
> durable sufficiency receipt，因此保持隐藏直到 L3 以 source-gap receipt 提升。Web
> domains/freshness 由显式 policy-aware Web port 执行服务端冻结值；实现不能执行 domain policy 时不授予
> `web:fetch`。Learning Timeline、stream 与 Run API 共用单一公开投影，只保留安全 ID、状态、计数和
> reason code；模型正文不作为公共审计事件。由于当前 MCP descriptor 不能证明工具只读，Learning
> Scope 暂不开放 MCP，且 Learning 路径不会读取 MCP catalog、server、transport 或 tool metadata。
> 本候选不包含 L2 UI、真实 Provider 首轮、LearningPlan、Artifact、Practice 或 Mastery。
> Evidence/Memory read 是 receipt 内部 authority，不作为伪工具加入普通公共 Capability Registry。
> active `session_id` 通过 Learning repository 的 owner/workspace binding 反查；可变 Session JSON
> 只作为待校验投影，marker 缺失或降级会稳定拒绝，不能恢复普通 Coding 的写权限或 raw Run API。
> 进程重启且内存 runtime 不存在时，HTTP Timeline 先用认证 owner + `session_id` 查询 canonical
> active binding；active Learning 的 Session JSON 删除或损坏稳定映射为
> `learning_scope_validation_failed` 409，普通 Coding 的缺失 404 与损坏 500 兼容语义不变。
> Retrieval receipt 在进入 Timeline/TurnContextPlan 前收敛为实际可执行来源；model 前重验失败保留
> `learning_scope_*` reason，不包装为 Provider error。`knowledge=required` 且 Knowledge 当前不可用时，
> 在 Provider 前稳定返回 `learning_scope_source_gap`；Knowledge 已调用但 zero-hit 的完整 sufficiency
> 判定和有界 Web 提升仍属于 L3，L1 不宣称完成。

**交付行为**

- active task 冻结 `AllowedCapabilitySet`，模型可见工具和执行入口双重过滤；
- 默认允许 Knowledge/Evidence/Memory read；Research child 只继承当前可用的只读来源，直接 Web 仅在 Knowledge 被明确禁用时开放；
- Shell、Patch、Git write、删除、write-MCP、Practice 和自动长期写入默认不可见且不可调用。

**验收证据**

- `不要联网`、旧 capability revision、伪造 tool call 和 Skill 未激活均 fail closed；
- active Learning Session marker 被删除或降级时仍由 server-owned binding 识别，运行与 raw Run API 均 fail closed；
- L0 REST activation/resume 漂移返回明确 `409`；L1 已启动 run 的 admission、model、retrieval 或 tool 漂移返回稳定 `learning_scope_*` 事件/error receipt，不触发 Provider 或真实工具；
- 成功 Learning run 的 post-turn Goal evaluator 在额外 Provider call 前重新解析 active Task/receipt/Session/TurnContextPlan，任一 revision 漂移均不调用 evaluator；
- HTTP Timeline、WS replay 与 Run API 对 active Learning Session 使用同一公开投影；`run_started.surface_context/thread_goal` 与 `thread_goal_evaluated.evaluation` 不公开 query、source path、Goal/criterion、模型正文、Skill prompt 或网页正文；scope 漂移时 fail closed。
- no-runtime HTTP Timeline 在 active Learning Session 文件缺失或损坏时返回稳定 `learning_scope_validation_failed` 409；正常持久化 Learning 与普通 Coding 兼容路径均有真实 API 回归。

**当前验证边界**

- Learning 公共路径与拆分后的定向组：`42 passed`；
- L0/L1、activation/resume/concurrency、DeerFlow context、MCP、Goal、Runtime adapter、ToolBundle 与 Web 相邻超集：`288 passed`；
- 完整普通 Coding Routes：`58 passed`；
- Ruff、9 个改动 Python 文件 format check、Mypy（`247 source files`）和 `git diff --check`：通过；
- frontend private/public production build：通过；private build 仅有既有大 chunk warning；
- 固定 Python 3.12 与完整复现命令见 `2026-08-13-sage-recoverable-learning-task-v1-delivery.md` 的 Slice A3；
- 中枢最后 Runtime 短复审仍待执行，不能写成已关闭。

**依赖与非目标**

- 依赖 L0；复用现有 Permission/Policy/Approval/Sandbox，不新建权限系统。

## 11. 切片 L2：Assistant 任务确认与进入会话

> code candidate：`2ae52dfc47080a5349f2b3bbc00e9f182ecc1b8b`。当前只交付 Assistant 确认、durable kickoff accepted receipt 与共享会话入口，不包含 L3 Research/Artifact。初版候选复审的聚焦实跑为 `134 passed`，不是旧记录的 `131 passed`。

**交付行为**

- 在 Assistant 展示 draft、澄清项、来源策略和风险边界；
- 用户确认后只调用一次 activate；active receipt 后请求 durable kickoff，服务端确认 accepted 后才进入并显示共享会话；
- 失败保留 draft 和重试入口，刷新后恢复真实状态。

**验收证据**

- 前端覆盖 `draft/loading/needs_confirmation/activating/active/activation_failed/kickoff_dispatching/receipt_recovery_failed`；
- canonical `activating` 保持可恢复，active receipt GET 失败可显式重试；
- 首轮不得早于 kickoff accepted receipt，同一 task revision/activation 只接受并启动一次；
- 旧 Assistant/Coding 入口保持兼容；
- 仓库内 Playwright 覆盖创建、澄清、确认、失败重试、刷新恢复与 accepted 前后的一次启动边界。

**当前实现与验证**

- Assistant 默认学习模式接入现有 Learning draft/CAS/activate API，展示可编辑摘要、确定性澄清、完整 source policy 和浏览器安全风险提示；“直接对话”模式保留旧 Assistant 行为。
- store 覆盖 `draft/loading/needs_confirmation/activating/active/activation_failed/kickoff_dispatching/receipt_recovery_failed`，双击确认去重为一次 activate；同 revision 使用稳定 idempotency key，响应丢失后以 canonical Task/receipt 收敛。
- active activation receipt 后调用 `POST /api/v1/learning/tasks/{task_id}/kickoff`；服务端以 Learning SQLite + Session Journal 持久化 revision/key/session/content-bound receipt。响应丢失后 canonical GET 或同 key POST 返回同一 accepted receipt，进程重启可查询、继续或重放。
- kickoff accepted 后才选择并显示共享 Session；Coding WebSocket 以稳定 `turn_run_id` 启动一次。普通 Coding 的 `startSessionWithPrompt` 与 `UserMessage` 路径不变。
- 新 app 上的 WS 直接重连会从持久 Session 幂等 lazy rehydrate runtime；owner/auth、cursor 与恢复均在 `websocket.accept()` 前完成，浏览器握手成功即 runtime REST ready，不依赖整页刷新，普通 Coding 同样受保护。
- Provider pin、credential 读取、DNS pin 或其他 runtime 构造失败在 REST/WS 统一收敛为 `coding_session_rehydrate_failed`，固定响应不包含 secret 或内部异常；失败可重试。
- `CodingRunRegistry` 以 `session_id` 共享 run hydration task，短 guard 之外执行 `recover_interrupted_runs`，不同 Session 的磁盘恢复可并行，同一 Session 只恢复/发布一次。
- 外层完整 runtime reconstruction 共享 Task/result/error；当前 waiters 只执行一次，取消单 waiter 不取消 shared flight，最后 waiter/flight 完成后清理，失败后新顺序请求可重试。两层 guard 不嵌套执行 I/O。
- 同一固定 run 的 stale-check 竞态只在该 run 已有 durable events 时收敛为 replay；其他 active run、Thread Goal 或 Journal 冲突不被吞掉。
- receipt 存在但 canonical Task 缺失时，kickoff GET 与 WS 稳定返回 `learning_kickoff_binding_conflict`。
- 激活失败保留原 draft 字段和重试入口；编辑 failed draft 会经 CAS 形成新 revision，再使用新 revision key 激活。
- restart/reconnect、双层 single-flight、WS readiness、GET/WS 与完整 Coding Routes 定向 `80 passed`；9 个 Learning API/core 邻接文件 `76 passed`；Cloud/Coding/Thread Goal/Run Registry/Journal 邻接 `76 passed`。
- 聚焦前端 `6 files / 138 passed`；完整 Vue `69 files / 521 tests passed`。
- 全仓 Ruff/format（`483 files`）、pyproject 推荐 Mypy 范围（`249 source files`）、private/public production build 与 `git diff --check` 通过，private build 只有既有大 chunk warning。
- 仓库内 Playwright `1 passed`，隔离 FastAPI/Vite 与非敏感 fake provider 覆盖创建、三项澄清、activation 失败重试、kickoff dispatching 和 Assistant 刷新恢复；accepted 前 `turn_started=0`，accepted 后稳定 `turn_started=1`，Coding 刷新重连不重复。

可复现 E2E 命令：

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" \
  /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest \
  tests/api/test_coding_run_registry.py \
  tests/api/test_learning_kickoff_dispatch.py tests/api/test_coding_routes.py -q
PYTHONPATH="$PWD/packages/sage_harness:$PWD" \
  /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest \
  tests/api/test_coding_run_registry.py \
  tests/api/test_cloud_model_provider_routes.py \
  tests/api/test_coding_surface_context.py tests/api/test_coding_thread_goal.py \
  tests/core/coding/test_session_event_journal.py -q
/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m ruff check api/ core/ db/ evals/ tests/
/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m mypy core/ api/ packages/sage_harness/
npm --prefix frontend run test -- --run src/api/assistant.test.ts src/stores/assistantHome.test.ts src/views/AssistantHomeView.test.ts src/stores/coding.test.ts src/views/CodingView.test.ts src/components/coding/chat/CodingContextBudget.test.ts
npm --prefix frontend run test -- --run
npm --prefix frontend run build
npm --prefix frontend run build:public
SAGE_E2E_PYTHON=/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python \
  npm --prefix frontend run test:e2e
```

**依赖与非目标**

- 依赖 L0/L1；不重写 CodingView。
- `AssistantHomeView` 学习确认表单、`TurnInputKind/learning_kickoff` 类型合同、`LearningKickoffErrorCode` 与结构化 OpenAPI error responses 已在 L3 前置提交 `ace45c4` 收敛；不把该重构扩大到旧 Coding 入口。
- 不修改 L1 只读授权合同；不生成 LearningPlan、Task DAG、Artifact、Practice 或 Mastery。
- 当前结论是“code candidate 与 docs candidate 已完成最终复审并成为 L3 固定起点”，不是已合入 `dev/sage-v7` 或已发布。

## 12. 切片 L3：Research、Synthesize 与 Learning Artifact

> L3.1 code candidate：`dd9c93f`；mini-spec：`docs/superpowers/specs/2026-08-25-sage-learning-research-artifact-v1-mini-spec.md`。前两轮候选均未获三镜头放行，当前结论仅为“L3.1 已完成本地门禁，等待第八轮复审”，不是已放行、已合入、已发布或已证明真实 Provider/Web 质量。

**交付行为**

- LearningPlan/KnowledgeUnit 使用稳定 identity 和 source policy；
- Research 只收集 revision-bound hits/citations；Harness 统一组装 EvidenceBundle；
- Synthesize 生成带引用 Markdown/受控卡片 Learning Artifact；
- Knowledge 不足时返回 source gap，按策略有界触发 Web Research。

**公共 seam**

- LearningPlan/KnowledgeUnit schema；
- Learning Artifact Store：id/kind、Goal/Plan/Unit、content hash、media type、evidence refs、source revisions、status、idempotency key、retention；
- Resume Summary 只保存不透明 Artifact ref。

**验收证据**

- 无证据/过期 revision citation 为零；
- 重试和 Resume 不重复 Artifact；
- Web 禁用、Provider 失败、证据冲突和预算耗尽有确定性状态；
- 首个 Sage Task DAG/Checkpoint/Resume 学习材料可在 UI 查看来源。

**依赖与非目标**

- 依赖 L1/L2；不执行任意 HTML/JS，不自动写 Knowledge/Memory。

**当前实现与收口证据（2026-08-25）**

- Slice 1（`7997044`）：`advance` 在一个 SQLite `BEGIN IMMEDIATE` 事务内完成 expected checkpoint、task/plan/source/capability frozen binding 校验、durable request claim 和 fencing 获取。每个 request key 以 owner/workspace/task、expected revision、请求/响应 digest 和终态持久化；同 revision loser 在 Knowledge/Research/Artifact 外部副作用前被拒绝，历史成功 key 在后续推进后仍可重放。
- Slice 2（`bccb10d`）：Plan、Unit、Research receipt 与 Artifact identity 纳入 owner/workspace/plan scope；Plan hash 最终绑定 Unit IDs。SQLite reopen/read 重算 Plan、Unit、receipt、Artifact content hash/citation binding；篡改数据与旧空 identity fail closed，旧 Artifact 被 quarantine，不进入 API/UI。
- Slice 3（`5705d33`）：Knowledge 首次推进不依赖 Research profile；无 provider 仍可形成 canonical `source_gap/degraded/blocked`。条件 Research 复用合法 Harness child seam，真实执行 timeout 与 max-steps/token/tool budget，所有 gate/失败也保存含实际 usage/elapsed 的 receipt。sufficiency 复用既有合同；冲突来源保留双方 citation，Artifact 为 `unverified` 且不得 ready。
- Slice 4（`4848cf4`、`bbf7c19`）：Learning 失败码集中为可穷举枚举，L3 4xx/OpenAPI 使用统一结构；共享 UI 以 task + generation 拒绝迟到响应。Playwright 不再在浏览器用 `Map/page.route` 重写状态机，而是启动隔离真实 FastAPI + SQLite + 本地 fake Knowledge/Provider/Web，并以服务进程 PID 变化验证重启恢复。
- 第二轮修复（`40af474`）：request journal 增加可过期 lease、owner 与递增 fencing，failed/cancelled/orphan running 可接管，旧 owner 不能 complete 新 claim；最终 checkpoint CAS 重验完整 frozen binding。Research 使用单调 deadline 覆盖 executor、EvidenceBundle read 与冲突/sufficiency projection，timeout/cancel 终结 child 并留下 terminal receipt；Knowledge 与 Web evidence 合并后重新判定冲突。Resume 与 replay 重验跨 task Artifact、response digest/schema/canonical binding；Learning API 的 404/409/422/503 与 OpenAPI 同构，UI refresh 可接管旧 generation 并解除 busy。
- 历史第二轮 fixture 记录：B1/B2/B3、L0-L2 邻接与必要 Coding 定向 `136 passed`；最终 Research/Artifact/Execution/API focused `68 passed`；Vue 组件定向 `4 passed`；仓库化纵向 Playwright `3 passed`；当时记录的 Ruff/Mypy/private-public build 与 format 门禁通过。
- 历史第二轮 fixture 记录为 `527 passed` / `2154 passed`；L3.1 当前受控单线程 Vue 为 `71 files / 527 passed`，Python 全量为 `2168 passed, 12 skipped, 3 failed`。3 个失败均位于未被 L3 修改的 `tests/api/test_coding_context_routes.py`，固定 `b036b17` 对照同为 `3 failed, 11 passed`；本片不扩大为 Coding runtime 重构。
- 未证明：本地 fake Knowledge/Provider/Web 只证明协议、scope、幂等、冲突投影与重启恢复，不证明真实 Knowledge 检索质量、真实 Provider/Web 质量、学习效果、生产准确率或 SLA。
- Practice、Mastery、`code_test`、自动 Knowledge/Memory 沉淀、书本 RAG projection 修改和 B4 跨领域 Eval 均保持未交付。

## 13. 切片 L4：Practice、Mastery 与 Resume

**交付行为**

- 用户完成 code_test 后形成 PracticeReceipt；服务端验证、criterion 绑定、durable outbox 后进入 Mastery Ledger；
- 闭卷解释先形成带 model/rubric revision 的 Judge Artifact，不单独判定 mastered；
- 中断后分别校验三种 plan hash、owner、scope、Skill/MCP revision，再继续未完成单元。

**验收证据**

- 跳过 Practice 不写 Mastery；重复 receipt 幂等；失效 evidence 触发重算；
- 成功 child、Artifact 和 Evidence 在 Resume 后不重复；
- sidecar kill、刷新、进程重启、Approval pending 和 source revision 漂移均有覆盖；
- Timeline/Checkpoint 不保存大正文或 Judge 私密内容。

**依赖与非目标**

- 依赖 L3；开放题自动晋级 Mastery 明确后置。

## 14. 切片 M0：Memory 控制面

**交付行为**

- 在 Settings 展开现有 pending proposal 审批为 proposal/fact 搜索、详情、revision/event chain、更正 proposal、撤回和 recall receipt；
- 明确“撤回后 active recall 不可见”与“物理隐私删除”是两套合同；
- EpisodeReference 与获批 DurableEpisodeSummary 分开展示。

**验收证据**

- owner/workspace 隔离、CAS 冲突、冲突组原子返回、撤回后不再召回；
- recall receipt 展示 reason/provenance/token/latency，不暴露正文或 secret；
- 物理删除在定义审计保留与索引级联协议前不伪装成交付。

**依赖与非目标**

- 依赖 L4 的真实学习 episode；
- 导出、批量删除和 Procedural Skill 控制面可拆独立后续切片。

## 15. 切片 E0：学习与记忆 Eval

**交付行为**

- 先建立本地 Sage 长期学习消融：无记忆、现有 lexical、混合召回、分层加载；
- 再接 LoCoMo/LongMemEval 等公开 benchmark，固定模型、embedding、top-k、judge、数据版本和 token 成本；
- 30-50 条独立 review Gold 与产品功能分开交付。

**验收证据**

- write precision、false memory、Recall@K/MRR、Answer Correctness、temporal update、contradiction、abstention、Goal continuity；
- input/memory token、retrieval p50/p95、owner 越权率、deletion leakage 和 provenance coverage；
- 报告区分公开 benchmark、本地消融、确定性合同测试和真实 Provider 结果。

**依赖与非目标**

- 可在 L3 后开始 corpus/runner，最终结果依赖 L4/M0；
- 不把单一数字写成生产 SLA 或简历唯一论据。

## 16. 集成与发布收口

### I0：桌面首次学习 E2E

- D1/D2 与 L2-L4 合入同一候选 SHA；
- macOS 桌面端完成创建目标、确认计划、读取引用、code_test、关闭/崩溃、恢复、查看 Mastery 和 Memory Proposal；
- 使用 ego-lite/Playwright 验证 UI，使用真实 sidecar 进程和临时用户目录验证生命周期。

### R0：发布候选

- 后端门禁、Ruff、Mypy、前端测试/build、桌面 Rust tests、packaging smoke、`git diff --check`；
- Developer ID 签名、notarization、stapled DMG、Gatekeeper/quarantine 首启；
- 结构化诊断包脱敏审计；
- 更新 README、版本 Release、Obsidian `sage-learning` 的 source commit、证据、风险和下一阶段边界；
- 发布到 `main` 只使用已经在 staging 验证的同一不可变 SHA。

## 17. 升级给 CTO 的事项

只在以下情况暂停并请求决策：

- 需要 Apple Developer ID、notarization 或付费签名资源；
- Cloud OAuth provider 配置、域名或生产凭据缺失；
- 需要不可逆数据库迁移、用户数据物理删除策略或公开发布；
- sidecar 打包证明当前 Python 原生依赖无法可靠分发，需要切换 Electron/服务端路线；
- 产品方向、收费、隐私承诺或公开 SLA 发生变化。

## L3.1 第八轮复审固定点（2026-08-25）

- code candidate：`dd9c93f`；本地中枢复核已通过，docs candidate 随本段更新后单独提交；两者均只在本地，未 push、未建 PR、未合入。
- implemented：Knowledge coverage 不再用 `bool(citations)`，并以完整 token/CJK phrase 语义交集保守判定；首个 Knowledge search 前接入 scope revalidator barrier；Research receipt 对 HTTPS/domain/freshness、evidence ref/title/hash/time/kind/conflict group 做 canonical readback 校验；hard deadline 不叠加固定 cancel grace；SQLite connect/BEGIN 失败释放锁，API 将 sqlite 故障映射为结构化 storage-unavailable 503。
- fixture-verified：L3 focused `69 passed`；受控 Vue `71 files / 527 passed`；真实 FastAPI + SQLite Playwright `3 passed`；Python 全量 `2168 passed, 12 skipped, 3 failed`。
- baseline comparison：3 个失败仍是未修改 `tests/api/test_coding_context_routes.py`；固定 `b036b17` 对照此前同为 `3 failed, 11 passed`，本轮未扩大 Coding 隔离债务。
- not-proven：当前依赖组合全仓 Mypy 受既有 LangChain/LangGraph stub/API mismatch 影响；changed Research module targeted mypy 通过。fake Knowledge/Provider/Web 不证明真实质量、生产准确率、SLA 或学习效果；本地复核通过，但不等同于已发布或已合入。
