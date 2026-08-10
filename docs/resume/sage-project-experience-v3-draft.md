# Sage 项目经历简历稿 v3

> 事实基线：`dev/sage-v7@9b8c8de11a3e`，PR #136/#138/#140/#141/#143。
> 定位：两页简历投递稿；完整解释、指标边界和 PRD 见 [阶段总复盘](../evals/sage-harness-rag-stage-closeout-v1.md)。
> 状态：可用于下一轮 PDF 排版；端到端低样本结果只作为 Eval 设计证据，不包装为生产准确率。

## 推荐投递版

### Sage｜本地优先 Agent Harness 与可评测 RAG 工作台｜个人项目｜2026.06 - 至今

**项目简介**：面向开发者构建本地优先的 Coding Agent 与学习实践工作台，以统一 Harness 编排知识检索、工具执行和复杂任务，并将权限、状态与产物沉淀为可恢复、可复核的证据链。

**技术栈**：Python、FastAPI、LangGraph、LangChain、Pydantic、PostgreSQL/pgvector、SQLite FTS5、Redis、Docker、Vue 3

**项目链接**：[源码](https://github.com/ZeroMadLife/sage-agent)｜[技术博客](https://blog.sagecompanion.top/)

**核心设计与实现**：

- **模块化 Agent Harness 与有界编排**：分离通用 `sage_harness` 与 Sage 产品适配层，以 ports、middleware 和 capability registry 统一接入 Model、Tool、MCP 与 Skill；用不可变 `TurnContextPlan` 冻结本轮 scope，复杂任务由服务端校验 Task DAG 的依赖、预算与 hash 后按 ready wave 有界执行，子任务继续复用 Permission/Policy/Approval/Sandbox。结合 checkpoint、Timeline、Artifact 与 lease/fencing 支持恢复和旧 writer 拒绝；10 个 deterministic Runtime + Tool Stack 场景全部通过，task completion/policy compliance 均为 `1.0`。
- **PostgreSQL Agentic RAG 与证据门禁**：将 SQLite canonical truth 与 PostgreSQL 可重建 search projection 分离，质量路径采用 native `GIN + ts_rank_cd` sparse、pgvector exact cosine dense 与 RRF；以 Claim Sufficiency Gate 判断 EvidenceBundle 是否覆盖回答所需事实，证据不足时最多触发受控 Research child，仍不足则拒答。在 9 份固定快照、80 条版本化 Eval 上，语义候选相对 Hashing hybrid 将 Recall@10 `0.889 -> 1.000`、MRR `0.683 -> 0.806`、NDCG@10 `0.701 -> 0.852`，不包装为线上答案准确率。
- **分层 Eval 与架构决策门禁**：按 intent、retrieval、claim、generation、recovery、provider 和 latency 分层归因，使用 First-pass Claim Evidence Coverage、Answer Correctness、Correct Abstention/False Acceptance、Claim Recovery Gain 四项主指标；在两本真实长书、5,220 chunks 上验证 Query Rewrite 与 HNSW，因人工改写串行 P95 约增加 `1.8x`、HNSW 四档均无稳定延迟净收益，分别保持条件候选与 `keep_exact_hnsw_not_eligible`，避免为技术名词盲目增加线上复杂度。
- **分层安全策略与 Container Sandbox**：将参数校验、workspace path containment、Permission、Policy、Approval 固定在副作用前；Sandbox 采用 Rootless/Docker Desktop admission、seccomp、`cap-drop ALL`、`no-new-privileges`、只读 rootfs、禁网、CPU/RAM/PID/ulimit 与 mount 漂移校验，Docker live audit `10/10`。明确 workspace 仍为可写 bind mount，生产 image digest 与生产 rootless live audit 尚未收口。

## 相比 v2 为什么这样改

| v2 bullet | 问题 | v3 调整 |
| --- | --- | --- |
| 任务/学习意图 admission 与可恢复 Harness | 意图、Context、模块化、恢复和 benchmark 全挤在一条，读者抓不到关键决策 | 标题改为“模块化 Harness 与有界编排”；意图只作为 admission 边界，补上 Task DAG 与子任务权限复用 |
| RAG 混合检索与版本化 Eval | 只覆盖 80-case provider selection，缺少最新长书、Claim Sufficiency、Research child、Rewrite/HNSW 决策 | 改为“PostgreSQL Agentic RAG 与证据门禁”，同时保留最可信的 80-case 效果数字 |
| 上下文治理与证据链 | 本身正确，但在两页简历里会挤掉最新 Eval 决策 | Context/Artifact/Timeline 合并进 Harness；新增独立“分层 Eval 与架构决策门禁” |
| 分层安全策略与容器沙箱 | 仍然成立 | 保留并压缩，继续写清 writable workspace 和生产 rootless 边界 |
| 端到端 Eval `[待补]` | 现在已有 14-case E2E seed，但 Answer Correctness、Provider Failure 和 P95 尚不适合写成亮点 | 不填一个漂亮总分；改写为“已搭建分层归因与决策门禁”，低样本结果留在面试展开 |

## 更短版

PDF 如果版面不足，可压成下面四条：

- 拆分通用 `sage_harness` 与产品适配层，以不可变 `TurnContextPlan` 固化本轮能力与权限 scope；Task DAG 由服务端校验依赖、预算和 hash 后有界并行，子任务复用 Permission/Policy/Approval/Sandbox，结合 checkpoint、Timeline 和 fencing 支持恢复。
- 构建 SQLite canonical truth + PostgreSQL search projection 的 Agentic RAG，使用 `GIN + ts_rank_cd + pgvector exact + RRF` 和 Claim Sufficiency Gate；80-case frozen Eval 上 Recall@10 `0.889 -> 1.000`、MRR `0.683 -> 0.806`。
- 建立 intent/retrieval/claim/generation/recovery 分层 Eval；在 5,220 chunks 上因 Query Rewrite 延迟增加约 `1.8x`、HNSW 无稳定收益，分别保持条件候选与 exact 默认。
- 将参数校验、路径 containment、Permission、Policy、Approval 与 Container Sandbox 串成副作用前门禁，启用 seccomp、禁网、只读 rootfs、最小 capability 和资源限制，Docker live audit `10/10`。

## 面试中必须主动说明的边界

- 80-case 数字是固定语料的离线 retrieval/ranking 结果，不是线上回答准确率。
- 4-case Query Rewrite 是 `oracle_manual`，证明改写上界和代价，不证明真实模型改写质量。
- 14-case 长书 E2E Gold 是 `seed_manual`，可以讲评测设计与失败归因，不能讲生产准确率。
- HNSW 只在临时 `halfvec(2048)` 表达式索引中评测，运行时仍是 pgvector exact。
- Harness benchmark 使用 `ScriptedApiClient`；P95 `382 ms` 不是实时大模型 SLA。
- Sandbox live audit `10/10` 不等于内核级逃逸证明或生产 rootless 已验收。
