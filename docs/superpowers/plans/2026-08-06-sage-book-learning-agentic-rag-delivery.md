# Sage 长书学习与有界 Agentic RAG 实施计划

> 日期：2026-08-06
> 状态：Slice A/B/C/D 已完成首轮交付，Slice E 已完成 claim-aware 离线诊断，Slice F 已交付 4+2 Scorecard 与 clean 策略选择；下一阶段转向原子答案 Judge 实测与 rewrite 优化
> 基线：`codex/book-learning-rag-design@3902827`

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

真实 LLMWiki full receipt（clean source `d442b37`，FastEmbed + contextual，Doubao generator +
DeepSeek judge，14 case）已完成：11/14 case 完成 judge，provider failure 3/14，完成 case 的
unsupported claim rate 0、citation correctness 1、unanswerable correct abstention 1；
answerable 最终回答率 4/7，context precision/recall 0.1420/0.6429，P50/P95 59,647/120,385 ms，
token 171,486，cost 仍为 null（未冻结价格表）。这组数字是 seed 的端到端诊断，不是生产 SLA 或
泛化准确率。完整结果保存在 ignored `.coding/evals/book-learning-generation-full-d442b37.json`。

### Slice E：Claim-aware Sufficiency 离线诊断

- 新增 14-case / 15-atomic-claim 独立 gold，支持 `any/all` passage 绑定；跨人物、跨章节、跨书问题不再压成一句模糊 claim。
- 新增严格 loader、历史 retrieval/generation receipt adapter 和可单独复核的 CLI；provider failure 与质量分数分开。
- 指标新增首轮/最终 claim evidence coverage、bundle completeness、claim recovery gain、recovery resolution、answer readiness precision/recall、insufficient acceptance。
- clean retrieval Top-10 的 claim coverage / bundle completeness 为 `0.7333/0.7000`；真实 generation 完成子集为 `0.6190/0.5714`。
- 本轮实际 rewrite 的 claim recovery gain / resolution 均为 `0`，说明触发 recovery 没有补回 gold claim；4 个完整 bundle 全部放行、3 个不完整 bundle 全部拒答，离线 readiness precision/recall 为 `1/1`。
- Faithfulness 保持生成层独立 judge 指标：本轮为 `1.0`，但仅覆盖 4 个最终回答；不能用它替代检索完整性或 citation gate。
- 当前只接入离线报告，`online_gate_activated=false`；等 gold 扩充和 calibration/test 稳定后再决定是否接入 Coordinator。

### Slice F：四项核心 KPI 与检索策略选择

- 生成 Judge 接入原子 Gold Claim ID/statement，独立输出 covered、contradicted 和 unsupported；Answer Correctness 不再由 Faithfulness 或旧概括 claim 代替。
- Answer Claim Coverage 计算最终答案覆盖的必要 Gold Claim；Answer Correctness 要求最终决策为 answer、全部 Gold Claim 覆盖且没有矛盾。Provider failure 不进入质量分母。
- 新增 4+2 Scorecard：首轮证据覆盖、答案正确、拒答安全、二次检索收益，以及 Provider Failure/P95；其余指标降级为 diagnostics。
- clean source `3902827` 上，同语料、FastEmbed、Top-10 的五策略复跑后，contextual Claim Coverage/Recall 为 `0.7333/0.7500`，P95 `521.865 ms`；parent-child 同覆盖但 P95 `3509.691 ms`、chunks `25836`。
- 3 秒策略预算内，选择器将 `contextual_chunk` 标为 `offline_candidate`；没有修改线上默认 policy。Answer Correctness 首次真实数值仍等待新版 Judge full run。
- 完整指标定义、架构图、策略表和下一阶段边界见 `docs/evals/book-learning-scorecard-v1.md`。

## 不在本阶段承诺

- 不下载或提交来源不明的《凡人修仙传》《仙逆》TXT；
- 不把模型 confidence 当作事实正确率或忠实度；
- 不训练在线 SFT/RL；先建立标注 schema、可复现基线和离线评测入口；
- 不把实验候选或 seed benchmark 包装成线上准确率。

## 收口门槛

每个切片必须通过专项测试、`scripts/check.sh`、前端测试与两套构建（若共享 API 受影响）、
`git diff --check`，并在 Obsidian `sage-learning` 记录 source commit、测试证据、关闭风险、
遗留问题和下一阶段边界。
