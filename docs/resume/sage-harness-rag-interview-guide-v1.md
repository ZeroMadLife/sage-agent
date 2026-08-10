# Sage Harness / RAG / Sandbox 面试准备 v1

> 目标：从“会背组件名”升级到“能解释工程问题、关键决策、验证证据和未完成边界”。
> 使用方式：先闭卷回答，再对照“必须答出”；不要直接背下面的句子。

## 30 秒项目介绍

回答必须包含四层：

1. **问题**：Coding/学习 Agent 会同时遇到长任务状态、知识检索、工具副作用、上下文膨胀和失败恢复。
2. **架构**：Sage 用通用 Harness 统一编排模型、工具、RAG 和子任务，产品层只做适配。
3. **关键决策**：意图只收窄候选，Plan 冻结 scope，Permission/Policy/Approval/Sandbox 最终授权；RAG 用 PostgreSQL hybrid 和 Claim Sufficiency 决定回答或恢复。
4. **验证**：用版本化 RAG Eval、Harness benchmark、Sandbox live audit 和真实长书门禁做分层评测，而不是只看一次 Demo。

## 一次请求的 2 分钟主链

```text
User Turn
  -> TaskIntent / LearningIntent admission
  -> Retrieval Gate + ToolBundle selection
  -> immutable TurnContextPlan / plan_hash
  -> Harness Graph / Task DAG
  -> Model or Tool execution
  -> Permission -> Policy -> Approval -> Sandbox
  -> PostgreSQL hybrid retrieval -> EvidenceBundle -> Claim Sufficiency
  -> Answer / bounded Research child / abstention
  -> Checkpoint + Timeline + Trace + Artifact
```

## P0：必须答熟

### 1. 为什么要把 `sage_harness` 拆成独立 package？

必须答出：

- Agent Loop 的稳定职责是模型调用、工具发现与执行、middleware、checkpoint、事件和预算；Workspace、Knowledge、Sandbox 和前端协议是 Sage 业务适配。
- package boundary 防止通用运行时反向依赖产品模块，也避免每个 Agent surface 复制一套 loop。
- ports 负责依赖方向，adapter 负责接入真实实现，capability registry 负责可发现能力，middleware 负责横切治理。

常见误区：只说“解耦、提高扩展性”，却说不出究竟拆了什么依赖。

证据入口：`packages/sage_harness/`、`core/harness/`、`tests/harness/test_package_boundary.py`。

### 2. Task Intent 和 Learning Intent 为什么要分开？

必须答出：

- Task Intent 回答“这是回答、研究、改代码、审查还是教学”，用于收窄 Retrieval/ToolBundle 候选和风险提示。
- Learning Intent 回答“解释、比较、规划、练习还是研究”，同时决定 knowledge scope、direct/multi-hop、learning stage 和推荐模式。
- 两者都是 admission，不是 authorization；不能增加 Registry 中不存在的工具，更不能跳过 Permission/Policy/Approval/Sandbox。
- 当前默认是确定性规则，模糊时回退 `general/meta`；不为了意图识别额外调用小模型。

常见误区：把两个 Router 说成两个小模型，或者说“意图识别后就获得工具权限”。

### 3. `TurnContextPlan`、Checkpoint、Transcript、Timeline 有什么区别？

必须答出：

- Plan：本 Turn 的不可变选择和 authority snapshot，包含 scope、capability revision、limits 与 `plan_hash`。
- Checkpoint：Graph 下一步继续执行所需的动态状态和最小 Plan binding。
- Transcript：canonical 对话事实，不是权限来源。
- Timeline：用户可读的运行投影，不是恢复权威，也不能反推权限。
- Resume 必须同时验证完整 Plan 与 scoped Checkpoint；任一 mismatch 都 fail closed。

常见误区：说 Checkpoint 保存全部对话，或说 Timeline 可以恢复 Graph。

### 4. Task DAG 为什么不是让模型直接开多个线程？

必须答出：

- 模型只提出小型 JSON 计划，服务端负责 schema、依赖、环、profile、预算和 canonical hash 校验。
- Scheduler 按 ready wave 有界执行；最多 6 节点、并发 3，`practice` 节点独占 wave。
- 节点失败只阻断后继，独立分支继续；Resume 校验 `dag_hash/scope` 并复用稳定 child id/cache。
- 每个 child 仍经过 Permission/Policy/Approval/Sandbox，DAG 不是新的权限来源。

常见误区：把 `parallel_candidate` 说成已经并发，或声称支持递归 DAG、动态增图。

### 5. 为什么 PostgreSQL 质量路径使用 sparse + dense + RRF？

必须答出：

- sparse 的 `GIN + tsvector + ts_rank_cd` 擅长精确术语、代码标识符和稀有关键词。
- dense 的 pgvector cosine 擅长语义改写和同义表达。
- RRF 融合相对排名，不要求把两种异构分数强行校准到同一数值域。
- SQLite 保留 canonical truth/便携路径；PostgreSQL 是可重建 search projection，不能反向修改事实。

常见误区：把 `ts_rank_cd` 叫 BM25；把 RRF score 当概率；把 PostgreSQL projection 说成唯一事实库。

### 6. 为什么不直接默认 BM25、HNSW 或 Query Rewrite？

必须答出：

- BM25：当前中文长书同一 Gold 下没有超过 native hybrid，BM25 hybrid P95 反而达到 `2056.6 ms`，所以只保留可插拔实验路线。
- HNSW：5,220 chunks、2048 维下四档 Oracle Recall@10 都是 `1.0`，但没有稳定 P95 净收益，没达到“质量 >= 0.98、P95 <= 100ms、相对 exact 快 20%”的联合门禁。
- Query Rewrite：4 个困难 case 的人工改写提高 Claim Coverage，但串行 P95 约增加 1.8 倍，而且不是模型改写证据，所以只作为复杂问题条件候选。
- 工程决策看质量、延迟、成本、稳定性和可恢复性共同门禁，不看单一技术名词。

### 7. Claim Sufficiency、Faithfulness、Answer Correctness 有什么区别？

必须答出：

- Claim Sufficiency：回答前的 EvidenceBundle 是否覆盖当前问题需要的 Gold Claim。
- Faithfulness：回答里生成出的 Claim 是否被现有证据支持。
- Answer Correctness：最终回答是否覆盖正确 Gold Claim，且没有矛盾。
- Faithfulness 高只说明“没脱离给定证据”，如果证据找错或漏 Claim，答案仍可能不正确。

常见误区：用 Faithfulness 代替 Answer Correctness，或用 Recall@10 代替最终答案质量。

### 8. 为什么要同时保留 Chunk 和 Passage？

必须答出：

- Chunk 是物理索引单元：一个 embedding、一个检索候选、一个 citation。
- Passage 是稳定语义证据单元：Gold Claim 绑定章节/段落定位，不随 chunk 参数轻易漂移。
- Parent-Child 中可以检索 child、引用 parent passage；评测先把 chunk 投影到 passage 再计算覆盖。

### 9. Harness 的恢复为什么需要 lease/fencing？

必须答出：

- Checkpoint 只能说明从哪里继续，不能说明谁是当前合法 writer。
- lease 表示当前运行所有权；fencing token 单调变化，使旧进程恢复后也不能继续写新事件。
- cursor 只是读位置，不是所有权；WebSocket/SSE 断线不等于 run 被取消。

常见误区：把 cursor 当锁，或者认为客户端断线后服务端任务自动停止。

### 10. Sandbox 具体防什么？为什么仍不能说“绝对安全”？

必须答出：

- 上层先做 schema、路径 containment、Permission、Policy、Approval，限制“是否允许执行”。
- 容器层用独立/Rootless daemon、seccomp、`cap-drop ALL`、`no-new-privileges`、只读 rootfs、禁网和资源限制，限制“出错后能影响多大”。
- inspect 漂移校验避免复用同名但配置不可信的旧容器。
- workspace 仍是可写 bind mount，image digest 和生产 rootless live audit 未完成，也没有证明内核无逃逸漏洞。

## P1：指标与失败追问

### 11. 80-case 的 `0.889 -> 1.000` 到底是什么？

必须答出：20 条 frozen test 中 18 条可回答 case 的离线 retrieval Recall@10，对比的是百炼语义候选与 Hashing hybrid；它不是 80 条全部问题的线上准确率，且 test 没有 `semantic_paraphrase` case。

### 12. 为什么 14-case E2E 里检索更好，Answer Correctness 反而不一定更好？

必须答出：检索、Planner/Answer Provider、Prompt、Recovery、Judge 都有独立方差；dense-first 的 Recall/MRR/Claim Coverage 更高，但 Provider Failure `3/14`、Recovery Gain `0`，所以单次运行不能严格归因成检索导致的答案变化。

### 13. 你们的 4+2 指标是什么？

四项主指标：First-pass Claim Evidence Coverage、Answer Correctness、Correct Abstention/False Acceptance、Claim Recovery Gain。

两项运行指标：Provider Failure Rate、End-to-end P95。

Recall/MRR/NDCG、Citation Correctness、Faithfulness、Answer Relevance 用于进一步诊断，不取代主指标。

### 14. 如何避免调参污染 test？

必须答出：固定 corpus revision 和 leakage group；dev 只用于开发，calibration 只调 Gate，frozen test 只做最终报告；看过 test 失败后不能反调规则再继续声称 test 未参与 tuning。

### 15. 为什么 `oracle_manual` 仍然有价值？

必须答出：它估计“如果改写质量足够好，理论上能获得多少上界收益”，帮助判断是否值得做真实模型实验；但它不能证明模型能稳定生成同样质量的 rewrite，也不能直接进入产品默认。

### 16. 为什么 HNSW Recall@10 都是 1.0 仍然拒绝上线？

必须答出：这里的 1.0 是相对 exact Top-10 oracle 的一致率，只说明近似搜索没有丢 exact 排名；如果 P95 不降、索引更复杂且还用了 `halfvec(2048)` 有损 cast，就没有净收益。

## P1：安全与恢复追问

### 17. Permission、Policy、Approval、Sandbox 各自负责什么？

- Permission：当前 mode/session 允许哪类动作。
- Policy：根据工具、参数和风险规则做系统裁决。
- Approval：需要人确认的高风险副作用中断点。
- Sandbox：动作获准后，限制实际执行环境的可见性、权限和资源。

四层不能互相替代；意图路由也不属于其中任何一层授权。

### 18. 为什么 `cap-drop ALL` 之后还要 seccomp 和 `no-new-privileges`？

必须答出：capability、syscall、提权路径是不同攻击面。`cap-drop ALL` 去除 Linux capabilities；seccomp 限制允许的系统调用；`no-new-privileges` 阻止通过 exec 获得更高权限。需要叠加。

### 19. 只读 rootfs 为什么还允许 workspace 写入？

必须答出：Agent 的正常工作就是修改受控工作区，因此 rootfs 只读而 workspace 单独 bind mount 可写；这缩小写入面但不是完全只读隔离。下一步应使用只读 source + disposable overlay、配额和回收策略继续收紧。

### 20. Resume 时依赖发生变化怎么办？

必须答出：重新加载 frozen Plan，比较 prompt、Gate、scope、Tool/MCP/Skill catalog、Sandbox、limits 和 model；任何不可接受的 mismatch 返回固定 error code，停在最后可信 Checkpoint，不调用模型、工具或 Sandbox。

## P2：压力追问

### 21. 为什么不用 Elasticsearch、Milvus、Qdrant？

回答框架：当前规模、事务/citation 一致性、本地部署、运维复杂度、已有 PostgreSQL 能力；明确专用搜索/向量库不是永不使用，而是在规模、P95、隔离或多租户需求通过门禁后再拆分。

### 22. 如果数据增长到千万 chunk 怎么办？

回答框架：先做真实 scale curve 与 workload 分层；再评估分区、过滤选择性、维度、HNSW/IVFFlat、reranker、冷热层和独立向量服务；必须同时测 Recall、P95、build/update 成本和故障恢复。

### 23. 如果规则意图识别不够准怎么办？

回答框架：先扩大真实失败集和分类 schema；再增加可关闭的 advisory classifier；模型结果仍受显式约束和服务端 hard gate 覆盖，不能成为权限来源；保留 deterministic fallback 和版本化 receipt。

### 24. 如果面试官说“这些都是 AI 帮你写的”怎么办？

回答框架：不争论工具归属，直接讲自己能复现的工程决策；现场画完整链路，解释一个失败 case、一个 rejected candidate 和一个源码入口，并说明测试、PR、报告和未完成边界。

## 闭卷验收顺序

1. 白板画出完整架构，不看文档；
2. 任选 Harness、RAG、Sandbox 各回答 2 道 P0；
3. 用 Query Rewrite 或 HNSW 讲一次“为什么没有上线”；
4. 区分 Claim Sufficiency、Faithfulness、Answer Correctness；
5. 最后用源码定位并运行一条对应测试或 Eval。

达到标准：每题能在 60-90 秒内说清“问题、决策、实现、证据、边界”，不靠堆关键词。
