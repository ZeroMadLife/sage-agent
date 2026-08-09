# Sage 长书学习与有界 Agentic RAG 实施计划

> 日期：2026-08-06
> 状态：Slice A-G 已完成；豆包质量选型、bounded EvidenceBundle、Task DAG/Context 联合接线和 clean E2E 已收口
> clean eval 基线：`codex/book-learning-rag-design@3ee7111`

## 产品目标

用户只需要提出学习问题，不需要先挑书。Sage 默认从本地书架软路由相关来源，先做一次混合
RAG；证据不足或问题需要跨来源比较时，由 Leader 在预算内委派只读 Research children，
服务端合并带 citation 的 EvidenceBundle，再进行一次有界判断。没有新增证据、预算耗尽或
冲突无法解决时，系统停止升级并诚实拒答。

## 交付切片

### Slice A：合法长文本接入

- 支持 UTF-8/BOM TXT，按章节标题和空行段落形成稳定 blocks；
- `ParsedBlock` 保留 line、character、byte 区间，引用不伪造页码；
- parse artifact format v2 可读取 v1，旧解析结果不丢失；
- scanner/registry/source adapter 支持 `.txt`；
- 提交公共领域语料 manifest、许可、URL、SHA-256 和下载校验脚本，不提交原文。

完成证据：两本公共领域长书共 4,733,020 bytes，5,374 blocks，locator 覆盖和非空原文行
保留均为 1.000；详见 `docs/evals/book-txt-parser-v1.md`。

### Slice B：证据充分性合同

- 新增纯结构化 `RetrievalSufficiencyAssessment`；
- 明确 `answer / delegate_research / retry / abstain` 与 `round_index/stop_reason`；
- 模型判断只作为候选，真实 citation、来源覆盖、冲突、预算由服务端约束；
- assessment 不泄露原始 query、私有 book ID 或证据正文到公开 receipt。

完成证据：服务端纯合同已覆盖 answer/retry/delegate_research/abstain、第二轮无新增证据、
冲突与高模型 confidence 不得绕过 citation 的确定性回归。

### Slice C：有界 Agentic RAG 状态机

- 首轮 Knowledge RAG；
- 不充分时最多一次 Research 升级，最多 2 个并行只读 children；
- Synthesize 只读取服务器组装的 EvidenceBundle；
- 最多两轮检索；无新增证据、冲突未解或预算耗尽时停止；
- 记录 route reason、child count、retrieval rounds、token、latency、stop reason。

完成证据：Coordinator 已接入 `api/coding.py` 的 Gate 后、父 Harness 前；Agentic 综合和拒答
直接终止父模型路径，单轮 RAG 将书籍证据写入 durable context；外部 resume 不重新执行
Coordinator。Research 最大并发 2，Synthesize 只能读取服务端 EvidenceBundle，第二轮无新引用
或输出未引用 bundle 时拒答。

### Slice D：分阶段 Eval 与运行时接入

- parser：正文保留率、章节边界、locator 正确率；
- retrieval：Recall/MRR/NDCG 和跨章节 AllRecall；
- sufficiency：false acceptance、无意义升级率、停止原因；
- agentic：相对 single-pass 的 evidence coverage 增益、成本和 P95；
- 最后再加入 faithfulness、answer relevance、citation support 和端到端评测。

当前完成：parser 使用两本真实公共领域长书复跑；已建立 14 条 `seed_manual` query/gold，
FastEmbed + contextual 首轮 Recall@10 为 0.750、MRR 为 0.523；3 条可回答 recovery case 的
oracle rewrite 将 candidate evidence recall 从 0.1667 提升到 0.8889。Agentic evaluator 已覆盖
evidence coverage、false acceptance、unnecessary delegation、citation support、token、P95 和
stop reason，并新增真实 LLMWiki generation runner：生成模型负责 bounded planner/rewrite/answer，
独立 judge 负责 claim/citation/faithfulness/answer relevance 收据。未完成：seed 尚未扩展、
独立 review 或冻结；score-only 证据充分性 gate 尚未在真实书籍上校准；意图小模型/SFT/RL 尚未接入线上路径。

真实 LLMWiki full receipt（clean source `742f131`，FastEmbed + contextual、Top-10，Doubao generator +
DeepSeek judge，14 case）已完成：12/14 case 完成 judge，provider failure 2/14；完成 case 的
Answer Claim Coverage / Answer Correctness 为 `0.5000/0.5000`，unsupported claim rate 0、
citation correctness 1、unanswerable correct abstention 1；answerable 最终回答率 4/8，context
precision/recall 0.1441/0.6875，P50/P95 49,167/138,752 ms，token 216,095，cost 仍为 null
（未冻结价格表）。这组数字是 seed 的端到端诊断，不是生产 SLA 或泛化准确率。完整结果保存在
ignored `.coding/evals/book-learning-generation-742f131-top10-full.json`。

### Slice E：Claim-aware Sufficiency 离线诊断

- 新增 14-case / 15-atomic-claim 独立 gold，支持 `any/all` passage 绑定；跨人物、跨章节、跨书问题不再压成一句模糊 claim。
- 新增严格 loader、历史 retrieval/generation receipt adapter 和可单独复核的 CLI；provider failure 与质量分数分开。
- 指标新增首轮/最终 claim evidence coverage、bundle completeness、claim recovery gain、recovery resolution、answer readiness precision/recall、insufficient acceptance。
- clean retrieval Top-10 的 claim coverage / bundle completeness 为 `0.7333/0.7000`；真实 generation 完成子集为 `0.6667/0.6250`。
- 本轮实际 rewrite 的 claim recovery gain / resolution 均为 `0`，说明触发 recovery 没有补回 gold claim；5 个完整 bundle 中 4 个放行、1 个未放行，3 个不完整 bundle 全部拒答，离线 readiness precision/recall 为 `1.0/0.8`。
- Faithfulness 保持生成层独立 judge 指标：本轮为 `1.0`，但仅覆盖 4 个最终回答；不能用它替代检索完整性、Answer Correctness 或 citation gate。
- 当前只接入离线报告，`online_gate_activated=false`；等 gold 扩充和 calibration/test 稳定后再决定是否接入 Coordinator。

### Slice F：四项核心 KPI 与检索策略选择

- 生成 Judge 接入原子 Gold Claim ID/statement，独立输出 covered、contradicted 和 unsupported；Answer Correctness 不再由 Faithfulness 或旧概括 claim 代替。
- Answer Claim Coverage 计算最终答案覆盖的必要 Gold Claim；Answer Correctness 要求最终决策为 answer、全部 Gold Claim 覆盖且没有矛盾。Provider failure 不进入质量分母。
- 新增 4+2 Scorecard：首轮证据覆盖、答案正确、拒答安全、二次检索收益，以及 Provider Failure/P95；其余指标降级为 diagnostics。
- clean source `3902827` 上，同语料、FastEmbed、Top-10 的五策略复跑后，contextual Claim Coverage/Recall 为 `0.7333/0.7500`，P95 `521.865 ms`；parent-child 同覆盖但 P95 `3509.691 ms`、chunks `25836`。
- 3 秒策略预算内，选择器将 `contextual_chunk` 标为 `offline_candidate`；没有修改线上默认 policy。新版 Judge full run 已完成，Answer Correctness 仍低于离线目标。
- Top-10 generation 已完成并写入 4+2 Scorecard：Answer Claim Coverage/Correctness `0.5000/0.5000`，Provider Failure `0.1429`，P95 `138,752 ms`；Top-10 没有改善答案正确率，不能通过继续扩大上下文解决缺失 Claim。
- 完整指标定义、架构图、策略表、真实收据哈希和下一阶段边界见 `docs/evals/book-learning-scorecard-v1.md` 与 `evals/reports/book_learning_scorecard_v1_2026-08-08.json`。

### Slice G：真实 Embedding Tradeoff 与 Agentic 闭环收口

- 新增豆包 `doubao-embedding-vision-250615` 与百炼 `qwen3-vl-embedding` 的评测候选；纯 TXT 固定一个 chunk 一个向量，百炼显式 `enable_fusion=false`。
- 云向量使用模型 revision、role 和文本 SHA-256 绑定的 ignored SQLite 缓存支持断点恢复；缓存不保存正文、查询明文或凭据。
- 修正 benchmark runner 的 role-aware 预热，document/query 分开准备，避免把云 query 请求错误计入本地检索 P95。
- 同一 Gold/contextual/Top-10 下，FastEmbed/豆包/百炼 Claim Coverage 为 `0.7333/0.8333/0.7000`；豆包选为长书质量优先模型，FastEmbed 保留为无 Key 回退和消融基线。
- clean 豆包 retrieval 绑定 `3ee7111`：Recall@10 `0.8833`、MRR `0.7200`、NDCG@10 `0.7204`、SQLite P95 `5.174s`；质量选择成立，延迟门禁未通过。
- EvidenceBundle 限制为最多 12 条 evidence、每条 excerpt 最多 1,200 字符，优先覆盖不同 passage；它不改变 Top-K 和 citation 粒度。
- clean bounded generation 完成 13/14 case：Answer Correctness `0.4444`、Correct Abstention `1.0`、False Acceptance `0`、Claim Recovery Gain `0.1296`、Provider Failure `0.0714`、P95 `227.836s`。相同配置另一轮 Correctness 为 `0.6667`，答案质量尚未稳定。
- BookLearningCoordinator 的最终回答现在先完成 TurnContextPlan capture/compare 再返回，不增加父模型调用；Resume 仍不重复执行 Coordinator。
- 机器可读收据、架构图和指标解释见 `docs/evals/book-learning-embedding-provider-tradeoff-v1.md` 与 `evals/reports/book_learning_embedding_provider_tradeoff_v1_2026-08-09.json`。

## 不在本阶段承诺

- 不下载或提交来源不明的《凡人修仙传》《仙逆》TXT；
- 不把模型 confidence 当作事实正确率或忠实度；
- 不训练在线 SFT/RL；先建立标注 schema、可复现基线和离线评测入口；
- 不把实验候选或 seed benchmark 包装成线上准确率。
- 不把豆包质量选型写成生产 SLA；便携默认仍可离线启动，部署 profile 必须显式配置并继续承担 3 秒检索门禁。

## 收口门槛

每个切片必须通过专项测试、`scripts/check.sh`、前端测试与两套构建（若共享 API 受影响）、
`git diff --check`，并在 Obsidian `sage-learning` 记录 source commit、测试证据、关闭风险、
遗留问题和下一阶段边界。
