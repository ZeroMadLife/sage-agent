# Sage 长书学习与有界 Agentic RAG 实施计划

> 日期：2026-08-06
> 状态：Slice A 与 Slice B 已完成；Slice C/D 待后续小版本
> 基线：`codex/book-learning-rag-design@4837ca0`

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
- 不充分时最多一次 Research 升级，最多 3 个只读 children；
- Synthesize 只读取服务器组装的 EvidenceBundle；
- 最多两轮检索；无新增证据、冲突未解或预算耗尽时停止；
- 记录 route reason、child count、retrieval rounds、token、latency、stop reason。

### Slice D：分阶段 Eval 与运行时接入

- parser：正文保留率、章节边界、locator 正确率；
- retrieval：Recall/MRR/NDCG 和跨章节 AllRecall；
- sufficiency：false acceptance、无意义升级率、停止原因；
- agentic：相对 single-pass 的 evidence coverage 增益、成本和 P95；
- 最后再加入 faithfulness、answer relevance、citation support 和端到端评测。

## 不在本阶段承诺

- 不下载或提交来源不明的《凡人修仙传》《仙逆》TXT；
- 不把模型 confidence 当作事实正确率或忠实度；
- 不训练在线 SFT/RL；先建立标注 schema、可复现基线和离线评测入口；
- 不把实验候选或 seed benchmark 包装成线上准确率。

## 收口门槛

每个切片必须通过专项测试、`scripts/check.sh`、前端测试与两套构建（若共享 API 受影响）、
`git diff --check`，并在 Obsidian `sage-learning` 记录 source commit、测试证据、关闭风险、
遗留问题和下一阶段边界。
