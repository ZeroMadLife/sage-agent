# Sage v1.1.0

> Release date: 2026-07-28
> Release mode: 本地自用源码版本，不部署、不开放公网、不恢复飞书

v1.1.0 在 v1.0.0 的产品主线上收束 RAG 工程化能力。版本目标不是继续堆检索组件，而是让
检索选型、失败恢复、评测与证据边界可复现、可解释，并能明确说明哪些能力尚未验证。

## 版本入口

| 文档 | 内容 |
| --- | --- |
| [CHANGELOG](CHANGELOG.md) | 本版本交付与不交付的能力 |
| [TESTING](TESTING.md) | 自动化门禁与复现实验入口 |
| [REVIEW](REVIEW.md) | 架构取舍、风险和发布结论 |
| [RAG 工程化复盘](../../docs/evals/sage-rag-engineering-retrospective-v1.md) | 面试口径、指标含义与局限 |

## 交付主线

1. 以 9 份固定官方快照和 80 条版本化 Eval 建立可复现基线，按开发 40、阈值校准 20、
   冻结测试 20 隔离调参与最终验收。
2. 在 PostgreSQL 中实现 `GIN + ts_rank_cd` sparse、pgvector exact dense 与 RRF 融合；
   SQLite + Hashing 保留为离线回归和首次启动路径。
3. 在相同协议下比较 FastEmbed、百炼和豆包 Embedding；Provider 只在 selection 集选择，
   冻结测试只运行最终候选。
4. 通过 revision-aware Gate、HMAC retrieval trace、失败分类与有界 recovery，让拒答与恢复
   过程可观测。
5. 对 Parent-Child、语义切分、Contextual Chunk、Cross-Encoder、多模态证据合同和 HNSW
   规模门禁分别留证，不把候选实验写成默认能力。

## 当前边界

- 默认配置仍可使用 SQLite + Hashing；PostgreSQL 与云 Embedding 需要显式配置。
- 百炼是当前 Eval 胜出的候选，不代表已经部署或对所有语料长期最优。
- `citation support=1.0` 只说明 evidence 能解析回正确 revision，不代表生成答案正确率。
- 当前 Gold 由 AI/Codex 辅助构造并经 source-anchor、Schema、Hash、leakage-group 自动校验，
  没有独立人工逐条审核。
- 多模态仅完成 L1/L2 证据合同与合成评测；没有真实 VLM、视觉向量或 OCR 质量结论。
- 当前 generation 是确定性 extractive proxy；RAGAS 和真实 LLM generation 质量尚未接入。
- HNSW 在 100k synthetic 规模没有触发替换 exact 的阈值，因此未成为默认索引。
