# Sage Goal-Driven Chat Harness 后端契约审计

> 日期：2026-07-18
>
> 基线：`dev/sage-v7@9a4d803`
>
> 状态：H2.5C 实施前契约；P0-P3 分阶段向后兼容演进

## 1. 审计结论

Sage 已有可复用的单一 Harness Runtime、durable timeline、可恢复 checkpoint、
Knowledge revision-aware retrieval、稳定 citation、Wiki proposal、Memory proposal 和
异步 Knowledge Job。当前缺口不是再造一套 Agent，而是把 Thread、Goal、实际进入模型的
Research Context、Mastery Evidence 和长期 Proposal 统一到稳定契约中。

固定边界：

- 主对话、Knowledge 与 Coding Practice 共用 session、run、timeline、approval 和恢复协议；
- `surface_context` 只冻结用户选择，不得冒充模型已读取证据；
- Run Artifact 可自动保存，Wiki、Memory、Goal、Mastery 和 Plan 变更必须产生 Proposal；
- UI 七态只投影 timeline 与 connection state，不新增伪造的“思考状态”；
- LLM 可以提出变更，不拥有长期状态的直接写权限。

## 2. 已有能力与可复用边界

| 契约 | 当前事实源 | 可复用结论 |
| --- | --- | --- |
| Thread / Run | `CodingSessionStore`、`SessionEventJournal`、checkpoint | 保留 session 作为 Thread 兼容 ID；新增 Goal binding，不复制 runtime |
| Timeline | monotonic sequence、event ID、terminal-once、resume | 已满足有序、幂等、重放基础；新事件只追加 |
| Surface | `HarnessSurfaceContext` + 服务端 canonicalization | 已能冻结 page/node/source/file revision；仍缺实际检索回执 |
| Knowledge RAG | hybrid retrieval、token budget、page/source revision citation | 作为 Research Context Bundle 的证据解析底座 |
| Web Evidence | `search_web`、SSRF-safe `fetch_web`、run-scoped private artifact | 当前轮可引用；H2.5C 增加用户确认后的来源提案 |
| PDF parsing | durable Knowledge Job、MinerU ticket、Redis delivery、本地降级 | 继续由异步 Job 消费，不在对话请求线程等待 |
| Knowledge write | Wiki proposal + revision CAS + Git projection | 长期 Wiki 写入继续使用现有审批链 |
| Memory write | proposal-only port + revision CAS | 作为统一 Proposal lifecycle 的兼容实现 |
| Capability | registry、deferred promotion、MCP/Skill/Subagent | 作为 Harness 决策的能力目录，不成为状态事实源 |

## 3. 明确 Contract Gaps

### 3.1 P0：Thread-Goal 与 Context Receipt

当前 session 没有稳定 `goal_id`、`parent_thread_id`、`goal_contract_revision`；
`surface_context` 会进入 prompt，但没有 run/message 级冻结回执，也没有记录最终实际使用了
哪些 page/source/neighbor。前端在服务端回显冻结回执前只能表达“提交时冻结”，不能显示
“已冻结”。

扩展字段：

```text
ThreadBinding
  thread_id                existing session_id alias
  goal_id                  new
  parent_thread_id         new nullable
  goal_contract_revision   new
  learner_profile_ref      new nullable
  created_at               existing
  updated_at               existing
```

```text
ResearchContextReceipt
  context_receipt_id
  thread_id / run_id / message_id / goal_id
  surface / workspace_id
  selection_snapshot        selection type/id/revision
  selected_resource         resource type/id/revision
  goal_revision / graph_revision
  wiki_pages[]             page_id / revision / score / reason
  source_evidence[]        source_id / revision / citation_id / score
  graph_neighbors[]        node_id / relation / hop / score
  mastery_context[]
  token_budget / used_tokens / omitted_count
  warnings[]
  created_at
```

迁移策略：session API 保持兼容；新字段先 nullable。提交入口先 canonicalize 并持久化
`selection_snapshot/selected_resource/graph_revision` 的 frozen receipt，再启动 run；Context
Assembler 完成最终截断后补齐实际证据列表与 token 使用。候选检索结果不能计入
`wiki_pages/source_evidence`，后续节点切换也不得修改已绑定 run/message 的 receipt。

### 3.2 P1：Goal Contract、Mastery Evidence 与统一 Proposal

现有 Knowledge learning goal 是 Git-backed 单目标定义，尚不能表达多 Goal、能力权重和
可撤销的掌握证据。需要新增 Goal Contract 与 Mastery Ledger，不能直接把聊天轮数转成进度。

确定性进度规则：

```text
normalized_weight_i = weight_i / sum(active capability weights)
capability_score_i = deterministic reducer(valid, non-expired evidence)
goal_progress = sum(normalized_weight_i * capability_score_i)
```

- `weight` 必须为正；写入时归一化，不依赖 LLM 计算结果；
- evidence 失效、撤销或 Knowledge Unit revision 过期后重新计算；
- LLM 只能生成 `mastery_update` Proposal，正式 ledger 由审批或确定性规则更新；
- 重复 evidence 由稳定来源 ID 去重，不按消息次数累加。

统一 Proposal 最小字段：

```text
proposal_id / proposal_type / status / revision
thread_id / run_id / goal_id
reason / diff / impact_scope
evidence_refs[] / artifact_refs[]
base_revisions{}
created_at / updated_at / decided_at / decided_by
result_refs[] / last_error
```

H2.5C 的 `knowledge_source` proposal 是该契约的第一个来源型实现；Memory 与 Wiki
现有 API 保持不变，后续增加统一只读聚合视图，不先重写底层 store。

### 3.3 P2：Knowledge Unit 与分支研究

当前 graph 的 `page/source/project/concept/decision/tool` 主要描述内容与导航，不能直接代表
可学习、可验证的 Knowledge Unit。新增节点必须向后兼容，而不是重解释已有 page 节点。

```text
KnowledgeUnit
  unit_id / canonical_question / label / definition
  unit_type              concept | claim | method | comparison | case | question
  state                  proposed | verified | stale | disputed
  page_id / page_revision
  evidence_refs[] / relation_refs[] / goal_relevance[]
  mastery_summary
```

新关系使用 `prerequisite/supports/contrasts/applies_to/derived_from`；原 `WIKILINK`
继续保留为结构关系。显式分支研究创建 child Thread，并绑定父 Receipt；节点点击本身不创建 Thread。

### 3.4 P3：图谱增量与规模

当前 API 已支持 neighborhood/community/analysis，但缺少面向 1k/5k 节点的增量变更游标。
后续新增 `graph_revision + after_revision` 读取，不替换 sigma/graphology/ForceAtlas2。

## 4. H2.5C 来源提案契约

### 4.1 状态机

```text
run artifact
  -> pending proposal
  -> applying (CAS claim)
  -> approved + durable_job_id
  -> Knowledge job
  -> Wiki proposal

pending -> rejected
applying --safe failure--> pending(new revision + last_error)
```

`save_web_source` 只创建 `pending`；模型不能调用批准动作。批准后保存的是绑定
`canonical_url + content_hash + retrieved_at` 的不可变快照，不重新抓取最新页面。

### 4.2 Timeline / API 事件样例

```json
{
  "kind": "proposal",
  "status": "pending",
  "payload": {
    "type": "knowledge_source_proposal_created",
    "proposal_id": "ksprop_...",
    "proposal_type": "knowledge_source",
    "source_kind": "web",
    "requires_user_confirmation": true,
    "revision": 1
  }
}
```

```json
{
  "proposal_id": "ksprop_...",
  "status": "approved",
  "revision": 3,
  "result_refs": [{"kind": "knowledge_job", "id": "kjob_..."}],
  "content_hash": "sha256...",
  "canonical_url": "https://example.org/doc"
}
```

Timeline 只记录安全摘要和 opaque ID；完整正文、绝对路径、外部任务 ticket 和凭据均不进入事件。

## 5. 前后端责任边界

| 能力 | 前端 adapter | Harness / Knowledge / API |
| --- | --- | --- |
| 七态展示 | 按事件投影、布局、动效 | 提供有序事实事件、terminal、connection state |
| Goal 选择 | 表单与导航 | 校验 Goal revision、绑定 Thread |
| 节点点击 | 冻结 selection identity | 校验 revision、解析并生成实际 Context Receipt |
| Proposal review | diff 展示、approve/reject 操作 | 所有权、CAS、幂等、持久化与结果引用 |
| Tool/Subagent 过程 | 展开、跳转 operation ref | durable child/run/tool events |
| Mastery 进度 | 可视化 | 确定性计算与 evidence ledger |

## 6. 测试矩阵

| 场景 | 必须验证 |
| --- | --- |
| timeline 重放 | 相同 sequence/event ID 不重复投影，terminal 只出现一次 |
| 断线恢复 | `after=sequence` 恢复，不伪造 running |
| 重复 proposal | 相同 workspace/thread/run/artifact/hash 返回同一 proposal |
| revision stale | 旧 expected revision 返回 409，不发生快照或 Job 变更 |
| artifact 越权 | 跨 session/run 引用返回 404/拒绝，不泄露存在性 |
| artifact 篡改 | content hash 不匹配拒绝批准 |
| 检索为空 | Receipt status 明确 no_evidence，不能伪造 citation |
| 批准恢复 | materialize 或 enqueue 中断后可安全重试，不生成重复 Job |
| reject | pending 才可拒绝；重复决定冲突 |
| 权限失败 | owner/workspace/thread fail-closed |
| 分支合并 | child 只返回 artifact/proposal refs，不直接改父状态 |
| PDF | worker 不占用长 lease；MinerU 失败/超时走本地降级 |

## 7. 分阶段交付

1. H2.5C：Web Evidence 来源提案、CAS 审批、不可变快照、durable job；
2. P0：Thread-Goal binding 与实际 Research Context Receipt；
3. P1：Goal Contract、Mastery Evidence、统一 Proposal 聚合；
4. P2：Knowledge Unit typed graph 与 branch research；
5. P3：graph revision delta 与大图读取优化。
