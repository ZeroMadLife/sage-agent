# Sage 可恢复学习任务 V1 实施计划

> 日期：2026-08-13
>
> 状态：A1、A2 已迁移到 L0；A3 Runtime 修复候选 `9a454d24843dd27f2e2c00bb34366219c428675e` 仍待中枢最后短复审；A4/L2 `2ae52dfc47080a5349f2b3bbc00e9f182ecc1b8b` 已获 Runtime/Standards 放行，等待 Spec 快速复核；B-E 未开始
>
> 前置 PRD：`docs/superpowers/specs/2026-08-13-sage-recoverable-learning-task-v1-prd.md`
>
> 设计基线：`20b0a09dcdb74c463aa2a7a1ddc7ca5601b9676d`

## 0. 实施进度

| 切片 | 状态 | source commit | 当前事实边界 |
|---|---|---|---|
| A1 Draft 学习任务 | 已完成 | `c66abf9b94178bb744bf53d56501c12f77fd3071` | 可创建、读取和 CAS 修改草稿；确认前不启动 Runtime |
| A2 可恢复 Activation | L0 已迁移 | `6f84c8be881d041018b67bf54030f8bf9a9cf1f4` | 已绑定 Session、Thread Goal、Learning Goal Ref 和 kickoff TurnContextPlan；未生成 LearningPlan、Task DAG，也未执行首轮 Turn |
| A3 Learning allowlist | L1 Runtime 修复候选，待中枢最后短复审 | `9a454d24843dd27f2e2c00bb34366219c428675e` | active receipt 已接入模型 catalog 过滤、ToolNode/Goal evaluator 前 canonical 重验；no-runtime HTTP Timeline 在 Session 缺失/损坏时也按 active owner binding 稳定 fail closed；尚未合入 `dev/sage-v7` |
| A4 Assistant 确认 | Runtime/Standards 已放行，等待 Spec 快速复核 | `2ae52dfc47080a5349f2b3bbc00e9f182ecc1b8b` | Run hydration 与 runtime reconstruction 分层 single-flight；跨 Session 磁盘恢复并行，同 Session 共享结果/错误且取消隔离；普通 Coding 保持兼容 |
| B-E | 未开始 | - | Learning Map、Research、Resume Summary、Mastery 和 Practice 均未交付 |

A2 的恢复语义是 `durable bootstrap state machine + receipt + reconciliation`，不是
Learning SQLite、Session JSON 与 Journal 之间的跨存储事务。Task、activation 和
receipt 的 canonical scope 是 `owner_id + workspace_id`；`workspace_id` 由服务端从
canonical workspace path 派生，不接受客户端认领。

## 1. 交付目标

本计划描述完整可恢复学习任务的历史路线。当前 L0/L1 提供可恢复 bootstrap 与首轮只读范围，A4/L2 code candidate 提供 Assistant 确认和进入共享会话；Knowledge/Research、LearningPlan、Artifact、Mastery 和运行中 Resume 仍属于后续切片，不能按本文目标态视为已实现。

第一阶段交付两个入口，但只维护一套 Harness：

1. 自由主题学习地图：金融基础、阳明心学、Java 并发等领域共用同一任务合同。
2. RAG Eval 决策评审：使用现有冻结实验报告验证 Claim、Citation、论证和恢复，是首个可自动验收的学习样板。

Coding 仅作为按需启用的 Practice Engine，不改变普通学习任务的默认安全边界。

## 2. 外部最佳实践与 Sage 落地边界

本次只采纳可以转成 Sage 状态合同或测试的机制，不引入新的工作流引擎，也不复制外部项目的产品 UI 或 Agent 数量。

| 来源与固定 revision | 观察到的机制 | Sage 的具体落地 | 明确不照搬 |
|---|---|---|---|
| [Temporal samples-python](https://github.com/temporalio/samples-python/tree/05070f64efb66c628d0b1c049a375f3fbd341ce2)，`05070f6` | stable workflow ID、`update-with-start`、已存在实例复用、处理器验证和失败后可继续查询 | `task_id + idempotency_key`、activation intent、幂等 Session/Thread Goal 创建、active receipt 与启动时 reconciliation | 不引入 Temporal Server/Worker；不把 Sage 的跨 SQLite 存储假装成单事务 |
| [LangGraph](https://github.com/langchain-ai/langgraph/tree/644815f9e5bc52ad8f7a5227a456227e9c3e639b)，`644815f` | interrupt 后只从 checkpoint 的 next state 继续；checkpoint saver 有 conformance contract | 后续 Resume 分别匹配 `task_revision/learning_plan_hash/turn_context_plan_hash/dag_hash/checkpoint_scope/capability_revision`；L0 只生成 TurnContextPlan | 不替换 Sage 现有 Checkpoint、Timeline、Artifact 实现 |
| [DeerFlow](https://github.com/bytedance/deer-flow/tree/88252e9b318d34e7e1867155ad2c77993320788e)，`88252e9` | 工具在模型可见前过滤；实际调用前再次校验；Skill 可发现不等于本轮激活权限；策略解析失败时保留框架安全工具 | 服务端生成 `AllowedCapabilitySet`，对模型工具目录和执行入口双重过滤；学习任务禁止 Shell/Patch/Git write/write-MCP | 不复制 DeerFlow middleware matrix、UI、子 Agent 数量和完整 authz provider |

## 3. 交付合同

### 3.1 Learning Task 状态机

```text
draft
  -> activating
       -> active
       -> activation_failed -> draft（可重试）
active -> blocked -> active（用户明确继续）
active -> completed
任意非终态 -> archived（用户明确归档或补偿清理）
```

`activation_failed` 只表示 bootstrap 尚未完成。用户可以读取失败原因、修改 draft 或用同一 idempotency key 重试。孤立 Session 必须被标记为 archived，不能出现在普通最近会话列表。

### 3.2 Activation Receipt

激活是“对外原子、内部可恢复”的语义操作，不能声明跨 Session JSON、Journal 和 Learning SQLite 的数据库事务。服务端持久化 intent 后按固定顺序执行，并保存 receipt：

```json
{
  "version": 3,
  "workspace_id": "...",
  "task_id": "ltask_...",
  "task_revision": 1,
  "idempotency_key": "...",
  "session_id": "...",
  "thread_goal_revision": 1,
  "learning_goal_ref": {"goal_id": "...", "goal_revision": "..."},
  "learning_plan_id": null,
  "learning_plan_hash": null,
  "turn_context_plan_id": "turnplan_...",
  "turn_context_plan_hash": "sha256:...",
  "dag_hash": null,
  "capability_revision": "...",
  "allowed_capabilities": [
    "local:evidence_read",
    "local:knowledge_search",
    "local:memory_read"
  ],
  "source_policy_snapshot": {
    "knowledge": "preferred",
    "web": "forbidden",
    "domains": [],
    "freshness": "all"
  },
  "source_policy_revision": "lsrc_...",
  "resume_validation_version": "canonical_l0_v3",
  "receipt_status": "active",
  "created_at": "...",
  "completed_at": "..."
}
```

同一 `owner_id + workspace_id + task_id + task_revision + idempotency_key` 的重复请求只能返回同一 receipt；同一 workspace 内同一任务 revision 使用不同 key 不得产生第二个激活，不同 workspace 可以安全复用客户端 key。receipt 不保存 prompt、网页正文、Skill 内容、秘密或绝对路径。

expand-migrate-contract 期间，旧表新增 nullable `workspace_id` 只用于隔离迁移。旧 active 行只有在 canonical Session 和 TurnContextPlan 同时证明 owner、workspace、task revision、Plan identity、四维 source policy、catalog/capability revision 与 allowlist 时才回填并升级为 v3。新 receipt 标记 `canonical_l0_v3`；真实 v2 receipt 与旧 Plan 回填后标记 `legacy_l0_v2`，只按旧 Plan 当时存在的 retrieval/tools/resume 字段做等价 fail-closed 校验。迁移生成的 source snapshot/revision 是可审计的规范化证据，不声称 v2 曾冻结后来新增的字段。legacy draft、缺失资源或无法唯一判定的行保持不可见并标记 `blocked`，不能默认归入当前 workspace。

### 3.3 Capability Scope

`CapabilityRegistry` 是运行时能力目录，不是本次执行授权。每次激活由服务端根据 `task_kind=learning`、学习阶段、风险等级和用户约束生成不可变 `AllowedCapabilitySet`：

```text
intent（建议）
  -> task policy（冻结）
  -> AllowedCapabilitySet（本轮授权）
  -> Permission / Policy / Approval / Sandbox（执行强制）
```

L0 receipt 只冻结后续 L1 可使用的能力候选，不把候选接入模型或工具执行。L1 才能让模型看到 allowlist 过滤后的 schema，并在执行入口按同一 `turn_context_plan_hash + capability_revision` 重验。

### 3.4 Resume Contract

目标态 Resume 只接受服务端保存的 scoped checkpoint。当前 L0 尚未生成运行中 Checkpoint；`POST .../resume` 仅重新加载 canonical Session 与 TurnContextPlan，并要求以下已存在字段全部匹配：

- `task_id` 与 `task_revision`；
- `session_id/thread_id/run_id` 的所有权；
- LearningTask 与 receipt 双侧分别校验 `learning_plan_id/learning_plan_hash`、`turn_context_plan_id/turn_context_plan_hash` 和 `dag_hash`；L0 两侧都只能有 TurnContextPlan 身份；
- checkpoint scope 和 fencing token；
- `capability_revision` 与 `AllowedCapabilitySet`；
- catalog revision 与四维 `source_policy_snapshot/revision`（knowledge、web、domains、freshness）。

失败补偿归档 Session 前必须持有与 exact failed receipt 匹配的 activation fence；active commit 在同一 SQLite 写事务内重验并恢复 Session 可见性。这个合同只覆盖共享 Learning SQLite 与同一 storage 上的 L0 bootstrap writer，不宣称 PostgreSQL 后端、分布式文件系统或任意 Session metadata writer 已具备同等 fencing。

任一项漂移返回明确 `409`，不得静默重新意图识别、重新规划或扩大权限。断线不等于取消；取消必须产生终态事件并释放 lease。

## 4. 纵向切片

### Slice A1：Draft 学习任务可创建、修改、读取（已完成）

**交付行为**

- 接收自然语言主题和可选约束，创建 `draft`，返回确定性的澄清问题、风险提示和缺省来源策略。
- 用户可用 CAS revision 补充起点、期望结果、每周时间、截止日期、是否允许联网和金融教育边界。
- 用户确认前不调用模型工具循环、不创建 Session、不写 Mastery。

**公共 seam**

- `POST /api/v1/learning/tasks/draft`
- `GET /api/v1/learning/tasks`
- `GET /api/v1/learning/tasks/{task_id}`
- `PATCH /api/v1/learning/tasks/{task_id}` + `expected_revision`
- `LearningTaskContract` schema version 1；字段长度、枚举、时间预算和风险规则由服务端校验。

**验收证据**

- 金融输入出现教育边界和缺失目标提示，不出现个性化买卖建议。
- `不要联网`、`只用我的知识库` 被保存为硬约束；过期 revision 返回 `409`。
- 空主题、超长字段、非法时间预算和重复能力 ID 被拒绝。
- 同一 draft 的读取不改变 revision，进程重启后合同仍可读。

**依赖与非目标**

- 复用现有 `LearningGoal` 解析和 Knowledge workspace 身份，不修改 Mastery Ledger schema。
- 优先新增独立 Learning Task repository；不把任务合同塞进 `packages/sage_harness`，不迁移 `HarnessRunContext.surface`。
- 本片不创建 Session、不执行检索、不生成学习地图。

**验证**

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/api/test_learning_task_routes.py tests/core/learning/test_learning_tasks.py
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m ruff check api core tests
git diff --check
```

### Slice A2：可恢复 Activation，绑定 Session + Thread Goal（已完成）

**交付行为**

- 用户确认 draft 后，服务端以 idempotency key 启动 durable bootstrap state machine。
- 先保存 activation intent，再幂等创建/复用共享 Session、Thread Goal、Learning Goal binding 和 kickoff TurnContextPlan，最后写 active receipt。
- 任一步失败都能在同一 task revision 上重试或由启动 reconciliation 补偿；失败不生成第二个 Session 或 Goal。

**公共 seam**

- `POST /api/v1/learning/tasks/{task_id}/activate`
- `GET /api/v1/learning/tasks/{task_id}/activation`
- `POST /api/v1/learning/tasks/{task_id}/resume`
- `Idempotency-Key` 必填；`If-Match` 或 `expected_revision` 绑定 task revision。
- 响应返回 `workspace_id/task_id/session_id/thread_goal_revision/turn_context_plan_id/turn_context_plan_hash/catalog_revision/capability_revision/source_policy_snapshot/source_policy_revision/receipt_status`；expand 阶段另带 deprecated `plan_id/plan_hash` 等值投影。

**验收证据**

- 两个独立 repository/service/resources 实例共享 SQLite/storage 时，同 key 只产生一个 intent/Session/Goal/Plan，不同 key 只有一个 winner；迟到 failure writer 不能让 stage 从 active 回退。
- 在“intent 已写、Session 已建、Goal 已建、receipt 写入前”四个故障点注入异常，重启后 reconciliation 能完成或补偿。
- Session 创建后失败时被 archived；普通 session list 默认不展示孤立会话。
- Goal CAS、Learning Goal binding、TurnContextPlan hash 和 capability revision 彼此可追溯；`learning_plan_id/learning_plan_hash/dag_hash` 均为空。
- `/resume` 对 Session/Plan 删除或篡改、owner/workspace/task/catalog/capability/source policy 漂移返回稳定 `409`；L0 对非空 LearningPlan/DAG identity fail closed。

**依赖与非目标**

- 依赖 A1、现有 `CodingSessionStore`、`SessionEventJournal`、`ThreadGoalService` 和 `TurnContextPlan` 构造 seam。
- 不声称 SQLite 与 JSON 之间存在单事务；不改变旧 `/api/v1/coding/session` 响应合同。
- 本片不让 Assistant 发送首轮 prompt；先返回可确认的 kickoff receipt。

**验证**

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/api/test_learning_task_activation.py tests/core/learning/test_learning_task_bootstrap.py
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/api/test_coding_routes.py tests/api/test_coding_thread_goal.py
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m ruff check api core tests
git diff --check
```

### Slice A3：首轮 Learning Turn 只读 allowlist

> 2026-08-24 候选事实边界：代码候选固定为
> `9a454d24843dd27f2e2c00bb34366219c428675e`。当前职责分支实现了 `LearningReadonlyScopeResolver`
> 与 Harness middleware。Learning Session 在 model、host Memory retrieval、父 ToolNode 和
> Research child 的每次 model/tool 边界重载 L0 Task、receipt、Session 和 kickoff
> TurnContextPlan；模型 schema 与 deferred catalog 使用同一冻结 allowlist。Knowledge 读取与
> Memory recall 复用既有端口，Memory/Knowledge 长期写入、Shell/Patch/Practice/Task DAG 均不
> 开放。`knowledge=disabled` 时可按冻结策略直接开放只读 Web；
> `web=allowed_when_insufficient` 在 L1 尚无 durable sufficiency receipt，因此保持隐藏，等待 L3
> 以 source-gap receipt 提升。Web domains/freshness 由服务端冻结值覆盖模型参数。现有 MCP
> descriptor 没有可证明的只读副作用元数据，因此 L1
> Learning Scope 暂不开放任何 MCP，而不是把未知 MCP 猜成只读。本候选未实现 L2 UI、
> 首轮真实 Provider 生成、LearningPlan、Artifact 或 Mastery。
> `local:evidence_read` 与 `local:memory_read` 只表示 Learning receipt 内部 read authority，
> 不是可调用模型工具，也不写入普通 Session 的公共 Capability Registry 或改变其 revision。
> active Learning binding 由 repository 按 `session_id` 反查并校验 owner/workspace；Session JSON
> 中的 `session_kind` 与 `learning_*` 仅是待校验投影，缺失或降级不能回退到普通 Coding 权限。
> HTTP Timeline 在进程重启、内存 runtime 不存在时，会先用认证 owner + `session_id` 查询 canonical
> active binding，再读取 Session JSON；active Learning 的 Session 文件删除或损坏统一返回
> `learning_scope_validation_failed` 409，普通 Coding 缺失仍为 404，损坏仍保持既有 500 语义。
> Retrieval Gate 的 `selected_sources` 在写 Timeline 与 TurnContextPlan 前按同一 scope 收敛：
> `allowed_when_insufficient` 在没有 durable sufficiency receipt 时只声明并路由 Knowledge，
> 不再公开一个真实入口不可用的 Web source。model 前的 canonical 漂移保留稳定
> `learning_scope_*` 冲突，不包装成 Provider 故障。`knowledge=required` 但 Knowledge 当前不可用时，
> L1 只保证 Provider 前 `learning_scope_source_gap`；Knowledge zero-hit 后的完整 sufficiency receipt、
> Web 提升和引用完整性仍由 L3 实现。

**交付行为**

- 首轮 Turn 使用冻结的 `task_kind=learning`、`turn_context_plan_hash` 和 `AllowedCapabilitySet`。
- 允许能力可执行；Shell、Patch、Git write、删除、write-MCP、Practice child 和自动写 Knowledge 一律不可见且不可调用。
- Tool catalog 被篡改、能力 revision 变化或执行时 allowlist 不匹配时 fail closed。

**公共 seam**

- 扩展 `TurnContextPlan` / `TurnContextResume` 的 learning scope adapter，保留 `surface="coding"` 兼容字段，但不把它当 authority。
- 复用 `CapabilityRegistry`、`CapabilitySelectionIndex`、`ToolBundle` 和现有 Permission/Policy/Approval/Sandbox 链。
- 模型可见工具列表和执行前校验都受 `capability_revision`、`turn_context_plan_hash` 和 `task_id` 约束。

**验收证据**

- `不要联网` 的任务首轮不存在 Web/Research capability；`knowledge=required` 且 Knowledge 不可用时在 Provider 前返回 `learning_scope_source_gap`。Knowledge zero-hit 的 sufficiency 尚未实现。
- 伪造 tool call、改写 capability revision、重放旧 plan 均被拒绝，且不触发真实工具。
- 降级或删除 Session 的 Learning marker 仍由 active repository binding 识别并拒绝，Run API 不恢复原始 trace/diff。
- Skill 仅可发现但未激活时不扩大权限；策略解析异常时保留最小框架安全工具。
- Learning Timeline、Run List/Detail 和 workspace diff 公开投影不包含 query、source path、Skill prompt、网页全文或 token；原始 child trace 仍是 `.coding/` 内部恢复数据，不是浏览器合同。
- Learning stream、Timeline、Run List/Detail 和 workspace diff 复用同一个公开 projector；AIMessage 正文不进入 Learning 公共审计事件。
- HTTP Timeline 与 WS replay 会按 active Learning identity 投影真实 journal；`run_started.surface_context/thread_goal` 和 `thread_goal_evaluated.evaluation` 不再绕过 projector。scope 漂移时 HTTP 稳定 `409`，WS 在发送 payload 前以 `1008` fail closed。
- no-runtime HTTP Timeline 在 active Learning 的 Session JSON 被删除或损坏时稳定返回 `learning_scope_validation_failed` 409；正常持久化 Learning 仍可读取，普通 Coding 缺失/损坏保持原合同。
- Learning 完全跳过 MCP catalog port；不读取 server/transport/tool names，catalog 故障不能阻断 Learning。
- frozen domains 非空时，仅显式实现 policy-aware Web port 才能获得 `web:fetch`。

**依赖与非目标**

- 依赖 A2 的 active receipt 和现有 turn plan/resume 对比逻辑。
- 不把通用能力目录改成学习业务目录，不复制 DeerFlow middleware；只增加产品层 scope adapter。
- 本片只允许 source policy 下的有界只读 Research child；不做 L3 多轮 Research/Synthesize、Learning Artifact 或学习地图。

**验证**

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" \
/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest \
  tests/api/test_learning_timeline_projection.py \
  tests/core/harness/test_learning_scope.py \
  tests/core/harness/test_learning_public.py \
  tests/core/harness/test_learning_runtime_scope.py \
  tests/core/harness/test_learning_goal_evaluator.py \
  tests/core/harness/test_learning_web_policy.py

PYTHONPATH="$PWD/packages/sage_harness:$PWD" \
/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest \
  tests/api/test_harness_capabilities.py \
  tests/api/test_learning_task_activation.py \
  tests/api/test_learning_task_workspace_resume.py \
  tests/api/test_learning_timeline_projection.py \
  tests/api/test_coding_deerflow_context.py \
  tests/api/test_coding_surface_context.py \
  tests/api/test_coding_thread_goal.py \
  tests/core/learning/test_learning_activation_concurrency.py \
  tests/core/learning/test_learning_task_bootstrap.py \
  tests/core/learning/test_learning_tasks.py \
  tests/core/harness/test_capability_adapter.py \
  tests/core/harness/test_harness_runtime_adapter.py \
  tests/core/harness/test_learning_scope.py \
  tests/core/harness/test_learning_public.py \
  tests/core/harness/test_learning_runtime_scope.py \
  tests/core/harness/test_learning_goal_evaluator.py \
  tests/core/harness/test_learning_web_policy.py \
  tests/core/harness/test_retrieval_gate.py \
  tests/core/harness/test_subagent_adapter.py \
  tests/core/harness/test_web_fetch.py \
  tests/core/harness/test_web_search.py \
  tests/harness/test_capability_selection.py \
  tests/harness/test_skill_activation.py

PYTHONPATH="$PWD/packages/sage_harness:$PWD" \
/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest \
  tests/api/test_coding_routes.py

PYTHONPATH="$PWD/packages/sage_harness:$PWD" \
/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m ruff check api core packages tests

PYTHONPATH="$PWD/packages/sage_harness:$PWD" \
/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m mypy core/ db/ api/ evals/ public_agent/

npm --prefix frontend run build
npm --prefix frontend run build:public
git diff --check
```

当前 code candidate `9a454d24843dd27f2e2c00bb34366219c428675e` 的已执行事实：
Learning 公共路径与拆分后的定向组 `42 passed`；L0/L1、activation/resume/concurrency、
DeerFlow context、MCP、Goal、Runtime adapter、ToolBundle 与 Web 相邻超集 `288 passed`；
完整普通 Coding Routes `58 passed`。Ruff、9 个改动 Python 文件 format check、Mypy
`247 source files`、frontend private/public production build 与 `git diff --check` 均通过。
private build 仅有既有大 chunk warning。最终 Runtime 复审指出的 no-runtime P2 已形成候选，
中枢最后 Runtime 短复审仍待执行。

### Slice A4：Assistant 任务确认与进入会话

> 最终代码候选（2026-08-25）：`2ae52dfc47080a5349f2b3bbc00e9f182ecc1b8b`，仅本地 commit，未 push、未建 PR；Runtime/Standards 已放行，等待 Spec 快速复核。初版候选 `7df3d11a08398b91852d61da3e4fb8b2a64409d8` 的复审聚焦实跑为 `134 passed`，不是旧记录的 `131 passed`。

**交付行为**

- Assistant 展示 draft 摘要、澄清项、来源策略和风险提示；用户确认后只调用一次 activate API。
- 激活成功后请求服务端 durable kickoff dispatch；只有取得 revision-bound、idempotency-key-bound 的 accepted receipt 后才进入并显示共享会话。
- 激活失败时保留可编辑 draft 和可重试动作，不跳转到半初始化页面。

**公共 seam 与验收**

- 扩展现有 Assistant store/API，不改变旧 `startSessionWithPrompt` 兼容路径。
- 覆盖 `draft/loading/needs_confirmation/activating/active/activation_failed/kickoff_dispatching/receipt_recovery_failed` 状态；canonical `activating` 不被异常 catch 降级，active receipt GET 失败有显式恢复重试。
- 仓库内 Playwright 覆盖新任务、澄清、确认、失败重试、刷新后恢复；accepted receipt 前不得启动首轮，accepted 后同一 task revision/activation 只启动一次。
- 旧 Coding 首页和直接 Coding session 测试保持通过。

**当前实现与证据**

- Assistant 默认学习任务模式先创建服务端 draft，再展示可编辑主题、期望结果、澄清问题、来源策略和风险边界；显式“直接对话”模式继续复用旧 `startSessionWithPrompt`。
- Assistant store 以服务端 Task/activation/kickoff receipt 恢复 `draft/loading/needs_confirmation/activating/active/activation_failed/kickoff_dispatching/receipt_recovery_failed`；同一确认并发只发起一次 activate，并使用稳定 revision-bound idempotency key。
- active activation receipt 后，客户端调用 `POST /api/v1/learning/tasks/{task_id}/kickoff`；服务端以 Learning SQLite + Session Journal 持久化稳定 `dispatch_id/message_id/turn_run_id`。响应丢失后 canonical GET 或同 key POST 返回同一 accepted receipt，进程重启也可查询、继续或重放。
- 客户端只在 kickoff `accepted` 后选择并显示共享 Session；Coding WebSocket 消费 accepted receipt，以稳定 `turn_run_id` 启动一次。普通 Coding `startSessionWithPrompt` 与 `UserMessage` 路径不变。
- 服务端重启后，当前页面的 WS 可直接从持久 Session lazy rehydrate runtime，无需整页刷新或预先调用 REST resume；owner/auth、cursor 与 rehydrate 都在 `websocket.accept()` 前完成，握手成功即 runtime REST ready，普通 Coding 使用同一恢复边界。
- Provider pin、credential 读取、DNS pin 或其他 runtime 构造失败统一映射为 browser-safe `coding_session_rehydrate_failed`：REST resume 返回 `503` 结构化 detail，WS 返回 `1011` 固定 reason，不回显 secret 或内部异常。
- `CodingRunRegistry` 按 `session_id` 共享 run hydration task，应用级 guard 只发布 flight，`recover_interrupted_runs` 磁盘恢复在 guard 外执行，因此不同 Session 可并行；同一 Session 仍只恢复/发布一次。
- 外层完整 runtime reconstruction 也按 Session 共享 Task/result/error；当前 waiters 共享一次 bounded `503`，单 waiter 取消不取消 shared flight，最后 waiter/flight 完成后清理，后续顺序请求可重试。两层不持有对方 guard 做 I/O，不存在反向锁序。
- 两个连接命中同一固定 run 的 stale-check 竞态时，只在重新读取到该 run 的 durable events 后收敛为 replay；其他 active run、Thread Goal 或 Journal 冲突继续 fail closed。
- receipt 存在但 canonical Task 缺失时，kickoff GET 与 WS 均稳定返回 `learning_kickoff_binding_conflict`。
- activate 响应丢失时会重新读取 canonical Task/receipt；服务端已 active 则继续进入会话，真实 failed receipt 保留可编辑 draft 和同 revision 重试入口。
- restart/reconnect、双层 single-flight、WS readiness、GET/WS 与完整 Coding Routes 定向：`80 passed`；9 个 Learning API/core 邻接文件：`76 passed`；Cloud/Coding/Thread Goal/Run Registry/Journal 邻接：`76 passed`。
- 聚焦前端：`6 files / 138 passed`；最终完整前端：`69 files / 521 tests passed`。
- 全仓 Ruff/format（`483 files`）、pyproject 推荐 Mypy 范围（`249 source files`）、private/public production build 与 `git diff --check` 通过；private build 只有既有大 chunk warning。
- 仓库内 Playwright `1 passed`，使用隔离 FastAPI/Vite 与非敏感 fake provider，覆盖创建、三项澄清、activation 失败重试、kickoff dispatching、Assistant 刷新恢复；观测 accepted 前 `turn_started=0`、accepted 后稳定 `turn_started=1`，Coding 刷新重连不重复。

可复现 E2E 命令：

```bash
SAGE_E2E_PYTHON=/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python \
  npm --prefix frontend run test:e2e
```

**依赖与非目标**

- 依赖 A1-A3；使用现有 Assistant 入口和 Coding 页面作为会话承载。
- 不在本片重写 CodingView；只显示最小任务状态和恢复摘要。
- `AssistantHomeView` 大表单/确认区拆分登记为 L3 前技术债，本轮不做无关重构。
- `input_origin + emit_user_event` 收敛为 `TurnInputKind/learning_kickoff` 类型合同，以及 `LearningKickoffErrorCode` + 结构化 OpenAPI error responses，均登记为 L3 前技术债。
- 不生成 LearningPlan、Task DAG、Learning Artifact 或 Mastery Evidence，不实现 L3 Research/Artifact。

**验证**

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
SAGE_E2E_PYTHON=/Users/zeromadlife/Desktop/tour-agent/.venv/bin/python npm --prefix frontend run test:e2e
git diff --check
```

### Slice B1：Knowledge-only 学习地图与 source gap

**交付行为**

- 按 `Knowledge -> Claim Sufficiency` 读取个人来源，形成能力节点、前置依赖、阶段、首个任务和来源状态。
- 证据不足时输出 `source_gap`/`unverified`，不使用模型记忆伪造引用，也不把计划写成掌握证据。

**公共 seam 与验收**

- 复用 `KnowledgePort`、`EvidenceBundlePort`、`LearningIntentRoute` 和 Artifact Store。
- 新增 `LearningMapArtifact` schema version 1，绑定 task/source revision 和 evidence refs。
- 阳明心学本地无来源时返回缺口；有来源时每个 grounded 节点至少有一个可解析 citation。
- 同一 task revision 重放得到相同 canonical artifact hash；大正文不进入 Timeline/Checkpoint。
- 金融地图只描述教育目标和风险知识，不输出个性化证券建议。

**依赖与非目标**

- 依赖 A3；不修改书本 RAG 的 chunk/passage 语义和 PostgreSQL projection。
- 不触发 Web，不自动沉淀 Knowledge Source Proposal。

**验证**

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/core/learning/test_learning_map.py tests/evals/test_learning_map_contract.py
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/core/harness/test_evidence_bundle.py tests/core/harness/test_learning_intent.py
git diff --check
```

### Slice B2：条件 Web Research 与 citation

**交付行为**

- 仅当 source policy 允许、Knowledge sufficiency 不足且预算/风险规则通过时，创建只读 Research child。
- Research 返回去重后的 Evidence Bundle；失败时保留已有证据并进入 `blocked/provider_unavailable`。

**公共 seam 与验收**

- 复用 Web Search/Fetch port、Research subagent、Task DAG、Artifact Store 和 citation parser。
- Research child 绑定 task revision、parent run、capability revision、query receipt hash 和预算。
- `不要联网`、域名和 freshness 约束不可绕过；timeout、空结果、冲突和超预算均有确定性状态。
- Citation 绑定 URL/title/content hash/fetched_at；长期 Knowledge 必须走 Source Proposal 审核。

**依赖与非目标**

- 依赖 B1；不引入新 Research runtime，不做无限搜索，不复制网页全文到主上下文。

**验证**

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/core/harness/test_learning_research.py tests/core/harness/test_web_fetch.py tests/core/harness/test_evidence_bundle.py
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m ruff check core api tests
git diff --check
```

### Slice B3：LearningMap Artifact + Resume Summary

**交付行为**

- 阶段结束、断点或阻塞时保存 Resume Summary：目标、task revision、阶段、证据计数、缺口、阻塞原因和下一动作。
- 刷新或进程重启后从同一任务继续，不重新猜测目标或扩大权限。

**公共 seam 与验收**

- 复用 scoped Checkpoint、Timeline、Artifact refs、Thread Goal evaluation 和 revision comparator。
- 新增 `GET /api/v1/learning/tasks/{task_id}/resume` 浏览器安全投影。
- 在 Knowledge、Research、用户输入和 Approval 阶段注入断点；重启后恢复到正确 next action。
- task/plan/checkpoint/source/capability 任一 revision 漂移返回 `409`；过期 fencing token 不能覆盖新 checkpoint。

**依赖与非目标**

- 依赖 A2、A3、B1、B2；Resume Summary 不是跨任务 Memory，本片不实现 Mastery 晋级。

**验证**

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/core/harness/test_turn_context_resume.py tests/core/harness/test_learning_resume.py
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/api/test_learning_task_routes.py tests/api/test_learning_task_activation.py
git diff --check
```

### Slice B4：跨领域 E2E 与 V1 产品门禁

**交付行为与验收**

- 用金融基础、阳明心学、Java 并发和 RAG Eval 验证同一 Harness 的领域无关性。
- 每组覆盖“有 Knowledge”“无 Knowledge”“禁止联网”“条件 Research”“中断恢复”中的相关场景。
- 合同、来源状态、allowlist、Artifact 和 Resume Summary 均通过 schema 校验。
- 报告区分合同正确性、检索指标、生成质量和恢复质量，不把 fixture completion 写成真实学习效果。

**依赖与非目标**

- 依赖 A1-A4、B1-B3；不扩大到完整课程、长期掌握提升或公网多租户。
- 形成 `docs/evals/recoverable-learning-task-v1.md`，记录 case revision、split、失败注入和边界。

**验证**

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/evals/test_recoverable_learning_task.py tests/api/test_learning_task_e2e.py
npm --prefix frontend run test -- --run
npm --prefix frontend run build
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m ruff check api core packages tests
git diff --check
```

### Slice C1：RAG Eval 决策评审与 Claim Table

**交付行为与公共 seam**

- 读取冻结 Sage Eval 报告，生成 required/forbidden claims、hard negatives 和 citation checklist。
- 用户或 Agent 只在授权 Evidence Bundle 内形成 `ClaimTableArtifact` 和 `DecisionMemoArtifact`。
- 新增版本化决策评审模板与 Artifact schema；复用 B 阶段的 task、evidence、citation 和 resume seam。

**验收证据**

- 必须识别 `oracle_manual` 是上界、质量收益伴随串行 P95 增加，以及测试通过率不是答案准确率。
- Artifact 绑定真实 report revision；修改来源 revision 后旧评审不能继续执行。

**依赖与非目标**

- 依赖 B1-B3 的 Artifact/Evidence/Resume 合同，不修改现有书本 RAG 检索实现。
- 本片不写 Mastery、不自动调用 Practice、不把一个冻结样板包装成通用学习效果。

验证：

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/evals/test_rag_eval_decision_review.py tests/evals/test_book_learning_claims.py
git diff --check
```

### Slice C2：Bounded Defense、确定性 Gate 与认知候选

**交付行为与公共 seam**

- 针对缺失引用、错误归因、冲突和代价发起有界追问；超预算或无进展时停止。
- 确定性 Gate 检查 claims、citation、report revision、uncertainty 和 attribution；语义 Judge 只补充论证质量。
- 复用 Thread Goal evaluator、bounded continuation 和 Mastery outbox，新增浏览器安全的 `DefenseReceipt` 与 `CognitiveEvidenceCandidate` schema。

**验收证据**

- 输出 candidate 默认 `pending-review`；Judge/provider 失败时不得生成通过证据。
- 错误混淆 Parent-Child、Embedding Provider 和 Query Rewrite 收益时 attribution gate 失败。
- 答辩中断后从原问题和原 report revision 继续，不重复消费追问预算。

**依赖与非目标**

- 依赖 C1；确定性硬失败不能被语义 Judge 覆盖。
- 本片不把模型自评写成 `demonstrated`，不实现完整题库或考试系统。

验证：

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/evals/test_rag_eval_defense.py tests/core/harness/test_thread_goal_evaluator.py tests/core/harness/test_thread_goal_followup.py
git diff --check
```

### Slice D1：认知证据审核与恢复

**交付行为与公共 seam**

- 服务端提供 candidate 的 accept/reject/invalidate 审核动作，按 capability contract 检查证据类型。
- 复用现有 Mastery outbox/Ledger 的 idempotency、revision、invalidate 和 projection 公共 seam。

**验收证据**

- accepted candidate 幂等写入；重复审核不重复计数。
- 只有认知证据不能满足双证据能力；stale candidate、过期 revision 和跨任务 ref 被拒绝。
- reject 和来源失效均保留可追溯历史，恢复后不会绕过原审核决定。

**依赖与非目标**

- 依赖 C2；不改变既有 Mastery evidence 的不可变 provenance。
- 本片不创建 Practice evidence，不用阅读时长、聊天次数或模型置信度代替掌握证据。

验证：

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/core/learning/test_mastery.py tests/evals/test_rag_eval_mastery_gate.py
git diff --check
```

### Slice E1：可选 Practice child

**交付行为与公共 seam**

- 用户明确选择编程实践且阶段为 `apply` 时，以新 Plan revision 加入 Practice child。
- 复用既有 Practice child、文件/Patch/Shell 工具、Permission/Policy/Approval/Sandbox、测试和 Artifact receipt。

**验收证据**

- 讲解/计划阶段看不到写能力，Practice Turn 也只能使用新 allowlist 中的受控能力。
- 认知与实践双证据达到 contract 后才进入审核，不能由一个成功回答直接升级。
- Sandbox 资源上限、路径 containment、审批、中断恢复和终态清理回归通过。

**依赖与非目标**

- 依赖 A3 与 D1；Practice evidence 仍按现有 Mastery contract 记录。
- 不把 Sage 改回 Coding Agent 首页，不增加任意 Shell 权限，不为非编程领域默认启动 Sandbox。

验证：

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/core/coding/test_tool_executor.py tests/core/coding/test_permissions.py tests/core/coding/test_runtime_run_lifecycle.py
PYTHONPATH="$PWD/packages/sage_harness:$PWD" /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python -m pytest tests/api/test_coding_routes.py tests/api/test_coding_thread_goal.py
npm --prefix frontend run build
git diff --check
```

## 5. 依赖图与并行边界

```text
A1 Draft
  -> A2 Activation
       -> A3 Learning allowlist
            -> A4 Assistant entry
            -> B1 Knowledge-only map
                 -> B2 Conditional Research
                      -> B3 Resume Summary
                           -> B4 Cross-domain E2E

B3 -> C1 RAG decision review -> C2 Defense/Gate -> D1 Mastery review
A3 + D1 -> E1 Practice child
```

A2 是 A3/A4 的真实阻塞。B1/B2 不在 A3 权限冻结之前集成真实 Research。C/D/E 不阻塞首个自由主题学习地图，但必须复用 B 的 Artifact、Evidence 和 Resume 合同。

## 6. 兼容与文件所有权

- 根目录继续固定在 `dev/sage-v7`；每个切片使用独立 `feat/`、`eval/` 或 `docs/` worktree，通过 PR 合入。
- 不修改 `HarnessRunContext.surface` 枚举；学习权限由 `task_kind` 和冻结 allowlist 决定。
- 不在 `packages/sage_harness` 放 `LearningTaskContract`；通用 package 只提供 ports、middleware、capability registry、checkpoint 和 adapter seam。
- Learning Task repository、artifact schema、API 和前端任务状态属于 Sage 产品层；Coding Session、Timeline、Mastery Ledger、Sandbox 保持兼容。
- 每个切片提交职责清晰的 commit；合入前执行相应测试、生产构建和 `git diff --check`。
- 阶段合入后更新 Obsidian `sage-learning`，记录 source commit、测试收据、关闭风险和下一阶段。

## 7. PRD 覆盖检查

| PRD 要求 | 负责切片 |
|---|---|
| 确定性澄清、任务版本和金融教育边界 | A1 |
| 首轮前绑定 Session、Thread Goal、Learning Goal、Plan 与权限 | A2-A4 |
| 对外原子、内部可恢复的 bootstrap 与 reconciliation | A2 |
| 默认只读、能力目录与本轮授权分离 | A3 |
| Knowledge-first、source gap、条件 Research 和 citation | B1-B2 |
| Learning Map、Artifact、Checkpoint 与恢复摘要 | B1-B3 |
| 跨领域合同和分层 Eval | B4 |
| RAG Eval Claim/Decision/Defense 样板 | C1-C2 |
| pending candidate、审核、失效与 Mastery 幂等 | C2-D1 |
| Coding 作为按需 Practice Engine 和双证据 | E1 |

## 8. 最终 V1 发布门禁

发布前必须同时具备：

1. A1-A4、B1-B4 的 API、合同和 Playwright 证据；
2. C1-C2 的冻结 report revision、required/forbidden claim gate 和 provider failure recovery；
3. D1 的 Mastery 幂等审核证据；E1 若未完成，发布文档明确 Practice child 尚未纳入 V1；
4. 后端全量测试、前端测试与生产构建通过；
5. `git diff --check` 通过，外部来源和指标边界写入评测报告；
6. 对外文档不把 seed fixture、oracle_manual、deterministic completion 或局部 P95 写成线上准确率/SLA。

建议收口命令：

```bash
bash scripts/check.sh
npm --prefix frontend run test -- --run
npm --prefix frontend run build
npm --prefix frontend run build:public
git diff --check
```

## 9. 当前停止点与下一阶段入口

A1 与 A2 候选来自 `feat/recoverable-learning-bootstrap-v1@20182e4`，已迁移到当前 L0 职责分支。当前系统能把确认后的草稿可恢复地
绑定到共享 Session、Thread Goal、Learning Goal Ref 和 canonical kickoff TurnContextPlan，并通过 active
receipt 对外投影；它不会调用模型、发送首轮消息、执行检索或写入 Mastery。

A2 候选分支的历史收口验证（不替代本次 L0 新基线验证）：

- 聚焦 Learning Task/Activation：`27 passed`；
- Coding/Harness 相邻回归：`132 passed, 1 warning`；
- 后端全量：`1983 passed, 12 skipped, 1 warning`；
- 前端：`69 files / 505 tests passed`，`build`、`build:public` 通过；
- Ruff lint、mypy、本次文件 format check 与 `git diff --check` 通过；
- 全仓 format check 仍有 6 个未修改 Harness 文件的既有格式漂移，本切片未扩大无关 diff。

L0 在 `docs/desktop-learning-product-design@5eb9020` 新基线上的验证：

- 原 27 个 Learning Task/Activation 用例加 1 个旧 receipt 兼容回归：`28 passed`；
- Coding、Goal、TurnContextPlan、Resume、Task DAG 和 Thread State 相邻回归：`144 passed, 1 warning`；
- 全仓 Ruff lint 通过，Ruff format check 为 `467 files already formatted`；
- Mypy 为 `235 source files` 无问题；
- private/public production build 均通过；
- `git diff --check` 通过；
- warning 为既有 GPT-2 fallback tokenizer 提示。L0 没有执行首轮 Turn，也没有生成 LearningPlan、Task DAG、Learning Artifact 或 Mastery Evidence。

L0 在 `c10e700` 固定起点上的复审补强验证：

- 先以指定 Python 3.12 与 `PYTHONPATH="$PWD/packages/sage_harness:$PWD"` 得到 5 个合同 Red：并发补偿后 Session 仍 archived、真实 v2 legacy 缺审计 marker、Task-side 三种未来 identity 被 Resume 接受；
- Learning Task/Activation 专项扩展到 `50 passed`，包含双 service 同 stage 补偿、active 后旧归档 fence、真实 a6d v2 receipt/Plan GET+Resume、旧 catalog 绑定缺失 blocked，以及 Task/receipt 双侧 identity fail-closed；
- Goal、Session Journal、TurnContextPlan、Resume、Task DAG 与 Thread State 相邻恢复回归 `125 passed`；
- 全仓 Ruff lint 通过，8 个改动 Python 文件 format check 通过，Mypy `235 source files` 无问题；
- private/public production build 与 `git diff --check` 通过；private build 只有既有大 chunk warning；
- 本轮仍停在 L0：没有执行首轮 Turn，没有生成 LearningPlan、Task DAG、Learning Artifact、Mastery Evidence 或运行中 Checkpoint Resume。

A4/L2 最终代码候选 `2ae52dfc47080a5349f2b3bbc00e9f182ecc1b8b` 已把 run hydration 与完整 runtime reconstruction 拆成两层 per-session single-flight：跨 Session 磁盘恢复并行，同 Session waiters 共享结果/错误且取消隔离；既有握手 readiness、bounded error、固定 run 并发收敛与 Task 缺失 fail-closed 均保留。Runtime/Standards 已放行，当前仅等待 Spec 快速复核；不能写成已合入 `dev/sage-v7` 或已发布。

下一步只在 A3 最后短复审与 L2 三镜头复审通过、按 PR 合入后进入 L3：实现真实 Knowledge/Research、LearningPlan、Synthesize 和 Learning Artifact。L3 前不开放 Coding Practice，不生成 Mastery Evidence，也不把当前 kickoff TurnContextPlan 说成完整 LearningPlan 或 Task DAG。
