# Sage RAG PostgreSQL 精确混合检索设计

> 日期：2026-07-27
> 基线：`dev/sage-v7@a6300a9`
> 阶段：PR-3

## 1. 决策

SQLite `KnowledgeStore` 与 Git Wiki 继续保存来源、proposal、审批、page revision 和 graph 的
canonical truth。PostgreSQL 只保存可以从 canonical revisions 幂等重建的检索投影，不在本 PR
迁移审核状态机，也不让两套数据库共同拥有同一事实。

`KnowledgeStore` 依赖 `KnowledgeIndexBackend` 协议：默认仍为 `LocalKnowledgeIndex`；显式配置
`KNOWLEDGE_INDEX_BACKEND=postgres` 时切换到 `PostgresKnowledgeIndex`。两种后端共享 chunk、
citation、filter、RRF、relevance policy 和 API 返回合同。

## 2. PostgreSQL 投影

```text
knowledge_index_documents
  -> knowledge_index_source_revisions
    -> knowledge_index_chunks
      -> knowledge_index_citations

knowledge_retrieval_runs  # PR-3 建表；PR-5A 扩展并接入完整观测
```

- `documents` 保存 workspace/page/current revision 与 visibility。
- `source_revisions` 保存来源 revision、索引状态、Provider revision 和错误摘要。
- `chunks` 保存稳定 chunk 元数据、应用层 `search_text`、generated `search_tsv` 与 pgvector
  `embedding`。
- `citations` 显式绑定稳定 citation ID、chunk、page/source revision。
- 所有主键和过滤索引都包含 `workspace_id`；检索先约束 workspace、visibility、source、revision
  和 active，再做 sparse/dense 排序。

跨 SQLite/PostgreSQL 不建立伪分布式事务。单 revision 投影在一个 PostgreSQL transaction 内原子
替换；SQLite commit 极少数情况下若在 PostgreSQL commit 后失败，投影可能暂时领先 canonical
truth。索引始终可丢弃重建，启动 backfill 和显式 rebuild 会按 canonical revisions 修复；该边界
必须进入恢复测试和运维文档。

## 3. Sparse、Dense 与融合

- 摄取时复用 `index_text()`，把 CJK bigram、英文词、代码标识符、配置键与路径 token 写入
  `search_text`，再用固定 `simple` 配置生成 `tsvector`。
- 查询复用相同 tokenizer，以 `websearch_to_tsquery('simple', ...)` 构造 OR query；用 GIN 做
  candidate match，`ts_rank_cd(..., 33)` 排序：bit `1` 按 `1 + log(document length)` 温和归一，
  bit `32` 只把分数缩放到 0-1。它是 PostgreSQL FTS ranking，不称为 BM25。
- dense 使用 `embedding <=> query_vector` 做 cosine distance exact scan，返回
  `1 - distance`；本阶段不创建 HNSW/IVFFlat。
- sparse/dense 各取 bounded Top-N，再复用应用层 RRF `k=60`；报告保留两路 rank/score。
- Hashing Provider 的产品 hybrid 继续只融合 sparse candidates；真正全库 dense-only 仅用于消融。

PostgreSQL 官方文档把 GIN 列为全文检索的首选索引，并定义 `ts_rank_cd` 为 cover-density ranking；
pgvector 官方说明没有 ANN index 时默认执行 exact nearest-neighbor search。实现和报告均保留这些
术语边界。

## 4. 迁移与恢复

1. `ensure_schema` 幂等创建 `vector` extension、表、外键、GIN 和 metadata B-tree indexes。
2. backfill 从 SQLite canonical revisions 逐 revision 投影；Provider model/revision 变化时重算。
3. `--force` 只删除目标 workspace 的派生投影，再完整重建，不修改 Wiki、proposal 或原始来源。
4. revision 失败不留下半批 chunks；错误只保存 bounded 类型/摘要，不记录 DSN 或凭据。
5. migration CLI 输出后端、revision/chunk/error 数、构建耗时和存储占用，支持重复执行。

## 5. Eval 合同

- PostgreSQL 与 PR-2 SQLite baseline 使用同一
  `sage-official-agent-fullstack-v1@2026-07-27.1`、同一 Provider 与相同 Top-K。
- 输出每路 Recall@10/MRR/NDCG@10、Gate、citation、P50/P95、case failure 和 SQLite delta。
- overall Recall@10 下降超过 2 个百分点即不通过；必须列出新增失败 case，不能用平均延迟解释。
- 存储报告同时记录 workspace row bytes 和共享 relation/index bytes，避免把共享表大小伪装成
  单 workspace 成本。
- Eval workspace 使用独立 ID，完成后清理行；报告固定 source SHA、dataset revision 和 schema
  revision。

## 6. 非目标

- 不迁移 SQLite canonical store、Knowledge Graph 或 ingestion job store。
- 不引入真实语义 Provider、Gate 重校准、query rewrite、Cross-Encoder 或复杂分块。
- 不建立 HNSW/IVFFlat，也不从 49 chunks 的延迟推导生产 SLA。
- `knowledge_retrieval_runs` 在 PR-3 只建立前向兼容 schema；完整写入与失败可观测性属于 PR-5A。

## 7. 官方依据

- [PostgreSQL 16 Full Text Search functions](https://www.postgresql.org/docs/16/functions-textsearch.html)
- [PostgreSQL 16 Ranking Search Results](https://www.postgresql.org/docs/16/textsearch-controls.html#TEXTSEARCH-RANKING)
- [PostgreSQL 16 Preferred Index Types](https://www.postgresql.org/docs/16/textsearch-indexes.html)
- [pgvector exact and approximate search](https://github.com/pgvector/pgvector)
- [pgvector-python Psycopg2 integration](https://github.com/pgvector/pgvector-python)
