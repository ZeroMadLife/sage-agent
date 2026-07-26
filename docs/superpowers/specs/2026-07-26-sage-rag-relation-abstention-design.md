# Sage RAG Relation Retrieval 与 Abstention 设计

> 日期：2026-07-26
> 基线：`dev/sage-v7@b1bedea`
> 状态：v1 实现完成，待 clean-tree 评测报告固化

## 1. 当前事实与问题

Sage 当前检索链为：

```text
FTS5 BM25 candidates
  + Dense cosine candidates
  -> Reciprocal Rank Fusion
  -> top-k
  -> token-bounded evidence bundle
```

这条链路没有 cross-encoder 或 LLM reranker。真实 `text-embedding-v4` 只在 Benchmark 显式
注入 Provider 时使用；应用启动时默认 `HashingEmbeddingProvider`，并把 hashing dense 候选
限制在 sparse 已命中的 chunk 内，因此产品默认不能宣称真实语义召回。

Knowledge Graph 已保存 immutable snapshot、page/source/concept node，以及 `WIKILINK`、
`SHARES_SOURCE`、`EVIDENCED_BY` edge，但没有进入 `KnowledgeStore.search/retrieve`。现有图主要
服务可视化和学习目标分析，不能回答关系检索问题。

20 条无答案题准确率为 0 的直接原因不是模型不会拒答，而是检索器只要存在任意正 cosine
或 FTS 候选就固定返回 top-k；RRF 只融合相对名次，不保留“第一名也很差”的绝对语义。
`assemble_retrieval_bundle` 又把任意非空 hits 标记为 `evidence_found`。

## 2. 外部方案与 Sage 取舍

- Microsoft GraphRAG 将非结构化文本投影为 entity、relationship、claim 和 community；local
  search 先定位查询实体，再组合相邻关系、community report 与原始 text unit。
- KG2RAG 先以语义检索产生 seed chunks，再执行 KG-guided chunk expansion 和 context
  organization。它与 Sage 已有 BM25/Dense/RRF、citation 和 graph snapshot 的边界更接近。
- HippoRAG 使用查询实体作为 Personalized PageRank seed，在一次检索中完成 multi-hop 扩散。
- RE-RAG 和 CRAG 都把 relevance evaluator 与相对 rerank 区分开：rerank 决定候选顺序，
  confidence gate 决定证据是否足以回答。

Sage 不直接引入完整 GraphRAG 数据面。Knowledge revision、citation、workspace scope 和
proposal-first 仍是 canonical boundary；图只是可重建、带证据的检索投影。

## 3. 目标架构

```text
query
  -> sparse + real dense seed retrieval
  -> route-aware fusion
  -> evidence relevance gate
       -> insufficient: no_evidence
       -> sufficient: graph seed expansion
  -> relation-aware fusion / organization
  -> bounded evidence bundle with citations and relation paths
```

### 3.1 Retrieval Foundation

1. 增加生产可配置的 OpenAI-compatible Embedding Provider；未配置时继续显式 hashing 降级。
2. index revision 同时绑定 embedding model/revision；切换模型后旧 ready revision 必须重建
   embedding，不能保留 ready 状态却返回空 dense channel。
3. 保留 sparse/dense 原始分数、route agreement 和 margin，供 Benchmark 校准；RRF 分数不
   直接当作概率。
4. relevance gate 配置与 `model_id + model_revision + benchmark_revision + corpus_revision`
   绑定。Embedding 或 active corpus 不匹配时拒绝检索，不能静默停用策略后假装拥有可信
   abstention。BM25 阈值不能从发布文档直接迁移到任意用户知识库。
5. 运行时先冻结与报告一致的原始 top-k，再执行 absolute-score gate；被拒绝的 hit 不允许由
   未进入校准报告的低排名候选补位。请求 `top_k` 不得超过策略校准时的 `top_k`。

### 3.2 Graph-assisted Retrieval v1

1. 支持 Obsidian wikilink 与指向当前 Knowledge page 的标准 Markdown link。
2. 仅使用 `WIKILINK`/显式引用类 edge 做 1-hop seed expansion；`SHARES_SOURCE` 和
   `EVIDENCED_BY` 不作为相关性传播边。
3. 每个 graph candidate 必须保留 seed citation、edge evidence、target page revision 和
   relation path。没有当前 evidence 或 snapshot stale 时跳过，不降级为无来源图命中。
4. graph expansion 在 relevance gate 之后执行，避免无答案查询因扩散获得更多伪证据。
5. 默认限制 seed 数、每 seed 邻居数、总 graph candidates 与 1 hop；不在 v1 引入无限遍历。

### 3.3 Semantic Relation Graph v2

后续增加实体、谓词和对象的结构化抽取：

```text
subject, predicate, object, aliases,
source_revision, block_id, citation_id,
extractor_id, extractor_revision, confidence
```

抽取结果是 derived projection，不自动升级为 canonical Knowledge。实体消歧、关系合并和低
置信边必须可重建并保留来源。只有 relation benchmark 证明 1-hop 不足后才引入 PPR 或 2-hop。

### 3.4 Global GraphRAG v3

Louvain community 已存在，但当前没有 evidence-bound community summary。只有 corpus-level
问题证明 local search 不足时，才增加带 revision 和 citation 的 community reports；不把现有
可视化 community 直接塞进模型上下文。

## 4. Benchmark 与消融

现有 200 条 Benchmark v2 继续评估 passage retrieval。新增 Relation Benchmark，至少保存：

```text
id, query, split, answerable, relation_kind,
seed_sources[], required_sources[], gold_paths[]
```

这一版只评估检索、关系路径和拒答门，不把回答生成质量混进同一个分数；
`required_claims / forbidden_claims` 留在主 Benchmark v2，后续由独立 generation slice 评估。

核心指标：

- passage `Recall@K / MRR / NDCG@K`；
- multi-hop `AllRecall@K`：同一查询全部 required passages 均命中；
- graph path precision、gold path recall、无证据 edge rate；
- abstention precision/recall/F1、answerable coverage、risk-coverage curve；
- P50/P95、Embedding/Rerank 调用数和 graph expansion 数。

消融固定为：

| 变体 | Sparse | Dense | Graph | Gate |
| --- | --- | --- | --- | --- |
| R0 | 是 | 否 | 否 | 否 |
| R1 | 是 | 是 | 否 | 否 |
| R2 | 是 | 是 | 是 | 否 |
| R3 | 是 | 是 | 否 | 是 |
| R4 | 是 | 是 | 是 | 是 |

阈值只在 dev split 校准，test split 只验收。现有 20 条无答案题太少，新增 relation near-miss、
同名实体、断裂路径和相似概念 hard negative；不得在 test 上反复调阈值。

## 5. 非目标

- v1 不宣称完整 GraphRAG、semantic triple extraction、PPR 或 community summary。
- 不把图扩展当 reranker；graph route 和 relevance score 分开记录。
- 不用单一 LLM judge 生成 relation qrels。
- 不在真实用户数据上关闭安全策略做 SWE-bench；Coding Benchmark 作为后续独立适配层。

## 6. 验收边界

本阶段只有在以下证据同时存在时才可写入简历：

1. 当前 commit、语料 manifest、relation dataset 和 Provider revision 可复现；
2. R0-R4 消融输出 retrieval、all-recall、abstention、延迟与逐 case path；
3. graph hit 100% 绑定当前 citation，workspace/revision 隔离测试通过；
4. test split 结果未参与阈值选择；
5. 真实 Provider 未运行时只报告离线机制结果，不写语义效果提升数字。

## 7. 一手资料

- Microsoft GraphRAG Overview：<https://microsoft.github.io/graphrag/index/overview/>
- Microsoft GraphRAG Local Search：<https://microsoft.github.io/graphrag/query/local_search/>
- KG2RAG：<https://arxiv.org/abs/2502.06864>
- HippoRAG：<https://papers.neurips.cc/paper_files/paper/2024/file/6ddc001d07ca4f319af96a3024f6dbd1-Paper-Conference.pdf>
- RE-RAG：<https://aclanthology.org/2024.emnlp-main.1236/>
- CRAG：<https://arxiv.org/abs/2401.15884>
