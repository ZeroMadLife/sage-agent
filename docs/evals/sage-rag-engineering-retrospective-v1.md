# Sage RAG 工程化总复盘 v1

> 日期：2026-07-28
>
> 集成基线：`dev/sage-v7@3875383730a47246c7ad18ad374c1ad1d1cd5aaf`
>
> 范围：PR #112、#115 - #123
>
> 发布边界：未部署、未接飞书、未合入 `main`

## 结论

Sage RAG 从 SQLite FTS5 + Hashing dense + RRF 的本地原型，演进为具备版本化
Corpus/Eval、PostgreSQL exact hybrid、真实语义 Provider 候选、分层失败 trace、最多
两轮恢复、多模态 evidence contract 和 ANN 规模门禁的可评测系统。

这次演进没有把全部候选默认开启：

- PostgreSQL GIN + pgvector exact 已实现为可选后端，默认仍是 SQLite；
- 真实语义 Provider 在 frozen test 上提升 Recall/MRR，但因专项 test coverage 不足保持 opt-in；
- bounded recovery 只在固定语料/Eval 上验证，默认关闭；
- Contextual metadata、Parent-Child、Semantic Boundary、Cross-Encoder 均未通过 selection 门禁；
- 多模态完成结构化证据链，但未评 live VLM，也没有视觉向量检索；
- 100k exact P95 未超过冻结 SLA，正式 HNSW 曲线未触发，运行时索引为 0。

## 版本化评测资产

`sage-official-agent-fullstack-v1@2026-07-27.1` 包含：

- LangGraph、PostgreSQL、pgvector、FastAPI 四个官方项目；
- 9 个审批快照：7 份官方文档/README、2 份官方仓库核心源码；
- 80 条人工案例，`dev/calibration/test = 40/20/20`，test frozen；
- 49 个 parser blocks；
- URL、version/commit、license、content hash、parser revision 的完整绑定。

历史 release Benchmark v2 的 16 文件 / 879 chunks / 200 queries 仍是独立历史证据，
不能与这套 official Eval 的指标拼接。历史 `0.578 -> 0.814` 比较的是 Hashing hybrid
与 `text-embedding-v4` hybrid，不是当前数据集，也不是相对 sparse baseline。

## 当前链路

```text
approved snapshot
  -> parser block / DOCX / PNG / optional L2 region
  -> chunk + revision + citation + page/bbox/media
  -> SQLite projection (default)
     or PostgreSQL projection
        -> GIN + ts_rank_cd sparse
        -> pgvector exact dense
        -> RRF fusion
  -> route-specific Gate
  -> optional bounded recovery (max two rounds, same Gate)
  -> context + stable citations
  -> answer or abstain
```

PostgreSQL 的关键词路线将 CJK bigram、代码标识符、配置键和路径 token 写入 generated
`search_tsv`，使用 GIN 与 `ts_rank_cd(..., 33)`。它不是 BM25。dense 路线使用 cosine
exact scan；RRF 融合 rank，避免强行统一 full-text 与 vector 的分数域。

## PR 交付

| 阶段 | PR / merge | 结果 |
| --- | --- | --- |
| PR-0 | #112 / `3e799ff` | 同步并收口 embedding、Gate、显式一跳关系扩展 |
| PR-1 | #115 / `9fbb74d` | 版本化 Corpus/Eval/Schema，9 snapshots、80 cases |
| PR-2 | #116 / `a6300a9` | SQLite sparse/dense/hybrid 分层 baseline 与 failure taxonomy |
| PR-3 | #117 / `ca75b71` | PostgreSQL GIN + pgvector exact + RRF，未建 ANN |
| PR-4 | #118 / `1af8605` | 真实语义 Provider selection/final 与 route-specific Gate v2 |
| PR-5A | #119 / `0369a66` | HMAC `retrieval_runs` 与分层失败 trace |
| PR-5B | #120 / `4e192cb` | 同一路 hybrid 的一次改写与有界 Top-K 恢复 |
| PR-6 | #121 / `a033504` | 四种分块/重排策略单变量消融，均不默认开启 |
| PR-7 | #122 / `800aad7` | DOCX/PNG/L2 region 多模态 evidence contract |
| PR-8 | #123 / `3875383` | 1k/10k/100k exact/HNSW 条件规模门禁 |

## 关键指标

### PostgreSQL exact 迁移

同一 80-case Hashing 数据集：

| route | Recall@10 | MRR | NDCG@10 | 对 SQLite 的解释 |
| --- | ---: | ---: | ---: | --- |
| sparse | 0.954 | 0.672 | 0.722 | Recall 持平；ranking 函数改变了排序指标 |
| dense | 0.612 | 0.365 | 0.412 | 与 Python cosine 完全一致 |
| hybrid | 0.928 | 0.686 | 0.727 | Recall 下降 1.974pp，通过预设 2pp 门禁，但不是无损迁移 |

三路 citation support/revision validity 均为 1.0。

### 真实语义 Provider

selection 的 58 个可回答样本上，PostgreSQL hybrid Recall@10 `0.940 -> 1.000`、
MRR `0.687 -> 0.791`。frozen test 的 18 个可回答样本上：

| metric | Hashing hybrid | Semantic hybrid |
| --- | ---: | ---: |
| Recall@10 | 0.889 | 0.944 |
| MRR | 0.683 | 0.771 |
| NDCG@10 | 0.701 | 0.782 |
| citation support | 1.000 | 1.000 |

false rejection 从 2 降到 1，没有新增 false acceptance；但 frozen test 的
`semantic_paraphrase` case 数为 0，专项启用门槛不可评估，因此 fail closed。

### 失败 trace 与恢复

- 80 case x 3 route 的 240 个 route-case 中，41 个失败行全部有唯一主要失败类型：
  retrieval 14、ranking 18、false rejection 4、false acceptance 5。
- 开启 trace 前后 Recall/MRR/NDCG 逐项相同；生产 trace 只保存 workspace-scoped HMAC
  指纹和有界候选元数据，不保存原始 query 或正文。
- bounded recovery 在 selection 的 58 个可回答样本上，将 Recall@10
  `0.939655 -> 0.974138`，两个已知 retrieval failure 清零，no-answer false acceptance
  保持 1；frozen test 原本没有 retrieval failure，只能证明无回归。

### 分块与重排

| candidate | selection 结果 | 决策 |
| --- | --- | --- |
| Contextual metadata | NDCG `+0.003015` | 低于 `+0.01` 目标 |
| Parent-Child | NDCG `+0.009783`；chunks `49 -> 109`；row bytes `2.031x` | 不降低门槛追认 |
| Semantic Boundary | 正式语料 0 个 block 超过 4000 字符 | 未触发，不能判定无效 |
| Cross-Encoder | NDCG `+0.045228`；Recall `-0.043103`；P95 `1435.744 ms` | Recall 与延迟失败 |

四个 candidate 均为 `eligible_for_default=false`。

### 多模态 evidence

12/12 synthetic fixture 通过，7 个 gold bbox case 区域准确率 1.0，citation identity
稳定。`block_kind/page/bbox/media_ref/confidence/parser_id/parser_version` 已贯穿
SQLite/PostgreSQL、API、Coding tool、Harness citation 与前端 Inspector。

这些结果只验证解析与 evidence contract；`live_vlm_quality_evaluated=false`、
`visual_vector_retrieval_enabled=false`。

### HNSW 规模门禁

固定 384 维 cosine、Top-K 10、32 queries、1 warmup + 2 measured passes：

| chunks | exact Recall@10 | P50 | P95 | total relation bytes |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 1.000000 | 0.585 ms | 1.403 ms | 2,252,800 |
| 10,000 | 1.000000 | 2.190 ms | 2.838 ms | 17,661,952 |
| 100,000 | 1.000000 | 57.411 ms | 93.906 ms | 172,228,608 |

门禁要求 exact P95 `>100 ms` 才运行正式 HNSW 曲线。100k 未触发，因此结论为
`keep_exact`。若未来触发，HNSW 还需同时满足 Recall@10 `>=0.98`、P95 `<=100 ms`，
并相对 exact 至少降低 20%。synthetic fixture 不是生产 corpus/query 分布或线上 SLA。

## 失败与拒答边界

线上没有金标，`gate_rejected` 不能直接称为 `false_rejection`。只有 Eval 才能把 failure
细分为 retrieval、ranking、context、false rejection、false acceptance、grounding、citation。

恢复只处理“有 active data，但结果占用不足，并且存在受评测术语改写”的 retrieval failure。
Round 2 使用同一 hybrid route、同一 Gate，Top-K 最大两倍且不超过 20；结果更差时回退
Round 1，仍不足就拒答。它不是无限 ReAct，也不解决 ingestion/ranking/generation failure。

## 简历可写与不可写

可写：

> 构建 9 份官方快照、80 条分层 Eval 的 PostgreSQL hybrid RAG，使用
> `GIN + ts_rank_cd + pgvector exact + RRF`；真实语义模型在 frozen test 将 Recall@10
> `0.889 -> 0.944`、MRR `0.683 -> 0.771`，citation support 保持 1.0，并以 selection/test
> 门禁决定语义 Provider、Cross-Encoder 与 HNSW 均不默认启用。

不可写：

- 线上召回率提升或生产 P95；
- HNSW、Cross-Encoder、Parent-Child 已上线；
- 100k 是真实生产知识库；
- fixture bbox 1.0 等于 OCR/VLM 质量 100%；
- `ts_rank_cd` 是 BM25；
- 已解决全部召回失败。

## 证据索引

- [SQLite layered baseline](knowledge-sqlite-layered-baseline-v1.md)
- [PostgreSQL exact](knowledge-postgres-exact-v1.md)
- [Semantic Provider + Gate](knowledge-semantic-gate-v1.md)
- [Retrieval observability](knowledge-retrieval-observability-v1.md)
- [Bounded recovery](knowledge-bounded-recovery-v1.md)
- [Retrieval ablation](knowledge-retrieval-ablation-v1.md)
- [Multimodal evidence](knowledge-multimodal-evidence-v1.md)
- [HNSW scale gate](knowledge-hnsw-scale-gate-v1.md)
- [Resume evidence draft](../resume/sage-project-experience.md)

## 下一阶段触发条件

- 补齐 semantic-paraphrase frozen test 后，才重审真实语义 Provider 默认启用；
- 增加真实 PDF/DOCX/PNG、扫描件和 live VLM 质量集后，才讨论视觉检索；
- 用真实 LLM 评 generation/grounding，不能继续依赖抽取代理；
- corpus 超过 100k、目标硬件或真实 filter 分布改变时重跑 scale gate；
- 只有 exact 超标才运行 HNSW curve，再进入过滤、增量写入与 canary 审查。
