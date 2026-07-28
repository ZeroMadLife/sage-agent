# v1.1.0 Architecture Review

## 结论

v1.1.0 的 RAG 链路已经达到秋招项目可答辩的工程完整度：有版本化数据、有 sparse/dense
混合检索、有 PostgreSQL 真实实现、有 Provider 对照、有 Gate 与失败恢复，也能从 trace 和
分层 Eval 解释召回、排序、拒答与 citation 问题。继续增加检索组件的边际收益低于学习 Sage
其他核心组件。

本结论只支持本地自用源码发布。它不支持公网部署、生产流量、线上 P95、多租户隔离或真实
LLM 生成质量声明。

## 关键取舍

| 决策 | 采用方案 | 没有默认采用的方案 | 原因 |
| --- | --- | --- | --- |
| 混合检索 | PostgreSQL GIN + exact pgvector + RRF | 将 `ts_rank_cd` 包装成 BM25 | 保持 PostgreSQL 16 可复现，并避免错误术语 |
| Dense Provider | 百炼作为当前 selection 结果 | 所有 Provider 并存在线路 | 在同一协议比较质量、延迟和成本；保留 FastEmbed 离线回退 |
| Gate | revision-aware policy | 跨模型复用阈值 | Embedding、维度和 role policy 改变会使分数分布漂移 |
| Recovery | 最多一次改写与一次扩大 Top-K | 无界 Agent 循环 | 让延迟、成本和失败语义保持可预测 |
| ANN | exact 为默认 | 直接启用 HNSW | 100k synthetic exact P95 未超过触发阈值 |
| Generation Eval | 暂不声称完成 | 用 citation 可解析率替代正确率 | 当前缺少真实 LLM、claim-level Gold 和独立人工复核 |

## 剩余风险

- v1 Eval 只有 80 条，frozen test 20 条且 semantic-paraphrase 覆盖不足；不能外推为通用 RAG
  排名。
- legacy `provenance=human_curated` 机器字段为保持冻结 Hash 未改；文档已经明确其真实含义。
- Eval 完整 JSON 约 22 MiB；v1.1.0 保留审计证据，下一 revision 按
  `evals/reports/README.md` 改用紧凑摘要与 CI artifact。
- 云 Provider 的可用性、价格和模型版本可能变化；生产使用必须重新校准并绑定 revision。
- 多模态、Dataset v2 与真实 generation 评测是未来研究项，不阻塞本地自用版本。

## 下一阶段

RAG 暂停新增功能。后续学习优先转向 Harness/runtime 生命周期、Context 与 artifact 治理、
工具权限与 Sandbox、Memory 生命周期，以及 Provider 容错与可观测性；这些组件更能解释
Sage 作为 Agent 系统而不只是 RAG Demo 的完整性。
