# Sage Learning Research / Artifact V1 Mini-Spec

> 日期：2026-08-25
>
> 状态：L3 实施合同
>
> 固定起点：`ca6e618d6df993dff40ed3a304ca942c239e7d84`
>
> 权威范围：桌面学习产品计划 L3；可恢复学习任务计划 B1/B2/B3

## 1. 问题与边界

L0-L2 已提供 owner/workspace scoped LearningTask、激活收据、只读 Learning Scope、
durable kickoff 和共享 Coding Session，但尚未形成可恢复的 LearningPlan、Knowledge-first
学习地图、条件 Research、citation-bound Learning Artifact 或运行中 Resume Summary。

L3 只交付从已确认目标到可审阅学习材料的只读链路，不执行 Practice，不生成 Mastery
Evidence，不自动写 Knowledge/Memory，也不把模型计划或 fixture completion 表述为掌握。

## 2. 复用边界

- Knowledge 读取复用 `KnowledgePort`，不得直读 Knowledge 数据库或修改书本 RAG
  chunk/passage/PostgreSQL projection。
- Research 执行复用现有 `SubagentExecutorPort` 的 `research` profile、Web Search/Fetch port
  和 `EvidenceBundlePort`；不得增加第二套 Research runtime。
- DAG identity 复用 `TaskDAGPlan` 的 canonical hash 规则；L3 的 V1 DAG 固定为
  `knowledge -> research(conditional) -> synthesize`。
- 大正文保存到 L3 Artifact Store。Timeline 和 Checkpoint 只保存安全摘要、计数、状态、
  revision/hash 和不透明 Artifact ref。
- 所有外部网页都按不可信数据处理，不执行 HTML/JS，不跟随网页指令，不自动进入长期
  Knowledge。

## 3. 状态机

一个 `(owner_id, workspace_id, task_id, task_revision)` 只有一个 active L3 execution：

```text
not_started
  -> knowledge_pending
  -> knowledge_ready | source_gap | blocked
  -> research_pending              (仅 policy + sufficiency + budget/risk 允许)
  -> research_ready | source_gap | blocked
  -> synthesize_pending
  -> artifact_ready | source_gap | blocked
```

可恢复断点为 `knowledge_pending`、`research_pending`、`user_input_pending`、
`approval_pending`、`synthesize_pending`。V1 的 Research 只读且不需要提升权限；
`approval_pending` 只恢复既有明确 approval，不能从 Timeline 猜测或自动批准。

`POST /api/v1/learning/tasks/{task_id}/advance` 每次最多推进到下一个 durable stage。
调用方可用相同 idempotency key 重试；服务端必须从 canonical checkpoint 决定 next action，
不能由浏览器指定 stage、query、capability 或 source policy。

## 4. Identity、revision 与 canonical hash

### 4.1 LearningPlan

`LearningPlan` V1 包含：

- `plan_id = lplan_<canonical_hash 前 24 位>`；
- owner/workspace/task/task revision 与 Goal ref/revision；
- source policy snapshot/revision；
- capability revision、catalog revision；
- 有序 KnowledgeUnit identity 列表；
- `dag_id/dag_hash`；
- `plan_revision=1` 与 schema version。

canonical payload 使用 UTF-8、JSON key 排序、紧凑分隔符；不包含时间戳、随机 ID、
模型正文或运行状态。`canonical_hash = sha256(canonical payload)`。同一 task revision 和冻结
输入必须得到相同 plan id/hash。

### 4.2 KnowledgeUnit

`KnowledgeUnit` V1 的 identity 由下列 canonical 字段计算：

- plan id、稳定 ordinal；
- 规范化标题、学习目标、前置 unit id；
- source policy revision；
- risk class。

`unit_id = lunit_<canonical_hash 前 24 位>`。Unit 状态只允许 `grounded`、`source_gap`、
`unverified`；这些状态不是 Mastery 状态。无证据、证据过期或冲突未解决时不得标为
`grounded`。

### 4.3 Evidence 与 Research receipt

Knowledge evidence 必须绑定 `citation_id/page_revision/source_revision/content_hash`。
Web evidence 必须绑定 `citation_id/url/title/content_hash/fetched_at`。缺失 identity 或 revision
的 evidence 不得进入 citation 集合。

Research receipt V1 固定：

- task/plan/unit revision；parent run id、child run id；
- capability revision、source policy revision；
- canonical query receipt hash，不保存未裁剪的用户正文；
- token/tool/child/time budget 与实际 usage；
- 允许域名、freshness 和 risk decision；
- terminal status、deterministic reason code、evidence refs。

`不要联网`、域名不允许、freshness 无法证明、timeout、空结果、冲突、Provider 不可用和
超预算都 fail closed。失败保留已有 Knowledge evidence，不伪造成 Research 成功。

### 4.4 Learning Artifact

Artifact Store 的一条记录包含：

- `artifact_id/kind/schema_version`；
- Goal、Plan、Unit identity/revision；
- `content_hash/media_type`；
- evidence refs/source revisions；
- `status=ready|source_gap|unverified|blocked`；
- revision-bound idempotency key digest；
- retention policy 与 created/updated timestamp。

内容仅允许受控 Markdown 和结构化卡片 JSON，不允许 HTML/JS。Citation parser 只接受当前
Artifact evidence set 内仍匹配 source revision/content hash 的 ref。过期、缺失或不属于当前
evidence set 的引用全部删除；结果 citation count 为零时 Artifact 必须为 `source_gap` 或
`unverified`，不能为 grounded ready。

`artifact_id = lart_<idempotency material hash 前 24 位>`。同一 task/plan/unit revision 重试、
进程重启或 Resume 必须返回同一记录；不同 content 对同一 idempotency material 是 409
contract conflict，不能覆盖。

## 5. Checkpoint、fencing 与 Resume Summary

Checkpoint V1 保存：execution identity、plan/dag hash、task/source/capability revision、stage、
next action、evidence count、gap/reason codes、artifact ref、lease owner 和 fencing token。
正文、query、网页内容和 Markdown 不进入 Checkpoint。

每次 checkpoint CAS 同时匹配：

- owner/workspace/task/task revision；
- plan/dag hash；
- source/capability revision；
- 当前 checkpoint revision；
- 当前 lease owner/fencing token。

新 writer 取得递增 fencing token 后，旧 token 的任何写入稳定失败且不能覆盖新 checkpoint。
task、plan、checkpoint、source 或 capability 任一漂移均返回 `409` 和结构化 reason code。

Resume Summary 只投影：

- task id/revision、目标安全摘要、plan/dag identity；
- 当前 stage、evidence/citation counts、gap/reason codes；
- next action；
- 不透明 Artifact ref 与 Artifact metadata；
- checkpoint revision/fencing token（只用于诊断，不授予写权限）。

`GET /api/v1/learning/tasks/{task_id}/resume` 设置 `Cache-Control: no-store`，以认证 owner 和
服务端 workspace 查询，不返回 topic 之外的用户正文、query、evidence excerpt、网页正文、
内部路径、原始 idempotency key 或 secret。

## 6. Public API / UI seam

- `POST /api/v1/learning/tasks/{task_id}/advance`：revision-bound、idempotent 地推进一个阶段；
  response 为与 GET 相同的 browser-safe Resume Summary。
- `GET /api/v1/learning/tasks/{task_id}/resume`：只读 canonical Resume Summary。
- Learning Artifact 内容通过 owner/workspace scoped artifact ref 读取；不得把文件路径作为 ref。
- Learning 共享会话展示真实 DAG 节点、checkpoint stage、Artifact 状态、来源标题/URL 或
  Knowledge citation identity。刷新与进程重启重新 GET canonical resume，不从本地状态猜目标。
- Coding 普通消息、L2 kickoff receipt、已有 Timeline 与 Run API 合同保持兼容。

## 7. Synthesize 规则

- 先由 Knowledge evidence 形成一到三个稳定 KnowledgeUnit；无来源时生成一个明确
  `source_gap` Unit，不让模型补 citation。
- 每个 grounded Unit 的 material claim 至少绑定一个可解析 citation。
- Markdown 固定包含目标、学习路径、Unit、来源与未解决缺口；金融主题只给教育目标与风险
  知识，不输出个性化证券建议。
- 冲突证据保留冲突标记和双方 citation，Artifact 为 `unverified`；不得通过选择性丢弃把冲突
  包装成已证实。

## 8. Failure contract

稳定错误码集中定义为枚举/类型合同，并进入 OpenAPI error response：

- `learning_research_policy_forbidden`
- `learning_research_domain_forbidden`
- `learning_research_freshness_unverified`
- `learning_research_timeout`
- `learning_research_no_evidence`
- `learning_research_conflict`
- `learning_research_budget_exhausted`
- `learning_research_provider_unavailable`
- `learning_artifact_contract_conflict`
- `learning_resume_revision_conflict`
- `learning_resume_fencing_conflict`
- `learning_resume_not_found`

错误 detail 只包含 `code/message/current_revision` 等受控字段，不回显 Provider/SQLite/文件系统
异常正文。

## 9. Public test seams

- dataclass/schema canonical identity 与独立预期 hash fixture；
- 临时 SQLite 的 plan/artifact/checkpoint repository，覆盖重复、重启、CAS 与 stale fencing；
- fake KnowledgePort + 真 EvidenceBundle 数据合同；
- 只在 Web/Provider/Subagent 外部边界使用受限 fake，覆盖 policy/domain/freshness/timeout/
  empty/conflict/budget；
- FastAPI TestClient 覆盖 owner/workspace、GET resume、409 drift 和 OpenAPI response；
- Vue 组件/store 覆盖真实 DAG/checkpoint/artifact/source 投影；
- 仓库化 Playwright 覆盖 Knowledge、source gap、条件 Research、失败、刷新/重启恢复、Artifact
  去重和 citation 展示。

## 10. Non-goals

- 不执行任意 HTML/JS，不开放写工具或扩大 L1 capability。
- 不做 Practice、Mastery、`code_test`、自动 Mastery 晋级。
- 不自动写 Knowledge、Source Proposal、Memory 或长期事实。
- 不修改书本 RAG chunk/passage/PostgreSQL projection。
- 不把 LearningPlan、Artifact status、模型回答或 fixture completion 写成学习效果。
- 不宣称 Web/Provider 可用性、在线 SLA 或生产准确率。
