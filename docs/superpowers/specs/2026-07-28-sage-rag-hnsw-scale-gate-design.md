# Sage RAG HNSW 规模门禁设计

## 背景

Sage 已有 PostgreSQL `GIN + ts_rank_cd + pgvector exact + RRF` 基线，但当前
49 chunks 的评测不能证明生产规模需要 ANN。pgvector 默认的 exact nearest-neighbor
搜索可作为召回真值；HNSW 通过近似搜索换取延迟，需要用 exact 结果校验
Recall，不能只看速度。

## 决策问题

1. `1k/10k/100k` chunks 下，384 维 cosine exact Top-10 的 P50/P95 是多少？
2. exact 是否超过冻结的 `100 ms P95` 交互式检索预算？
3. 只有超标时，HNSW 在多档 `ef_search` 下能否同时满足 Recall 与延迟门禁？
4. 实验结论是“保持 exact”，还是“允许进入后续生产 schema/filter/canary 审查”？

## 冻结契约

| 项目 | 值 | 原因 |
| --- | ---: | --- |
| scales | `1,000 / 10,000 / 100,000` | 覆盖小型到中型单 workspace 投影 |
| dimensions | `384` | 与当前 FastEmbed 真实语义 Provider 证据维度一致 |
| distance | cosine `<=>` | 与现有 dense route 一致 |
| Top-K | `10` | 与分层 Eval 主指标一致 |
| queries | `32` | 固定查询向量，兼顾 P95 样本量与本机成本 |
| warmup / measured passes | `1 / 2` | 报告热身后 64 次查询，降低单次抖动 |
| exact SLA | `P95 <= 100 ms` | 沿用 Sage 交互式 hybrid/recovery 阶段的检索预算 |
| HNSW build | `m=16, ef_construction=64` | pgvector 默认基线，不在本 PR 搜建索参数 |
| HNSW query | `ef_search=40/80/120/200` | 形成 Recall/P95 曲线 |
| HNSW quality | `Recall@10 >= 0.98` | 最多允许 2pp 的 exact-oracle 召回损失 |
| HNSW latency | `P95 <= 100 ms` 且相对 exact 至少降低 20% | 避免为无实质收益引入 ANN 复杂度 |

## 数据与真值

- 数据是可重建的 project-authored synthetic fixture，不伪装成生产流量。
- 每个查询有 10 个距离严格递增的标准近邻；干扰向量与查询使用不相交维度。
- exact Top-10 必须与生成器 gold 一致，同时作为 HNSW Recall@10 的 oracle。
- 专用 `UNLOGGED` 临时基准表使用随机后缀，`finally` 中显式删除；不向
  `knowledge_index_chunks` 注入合成数据，不改生产 schema。

## 测量方法

- 查询延迟：客户端 wall-clock P50/P95，另保存一次
  `EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON)` 的 server execution time、plan node 和
  shared/temp blocks。
- 内存：在权限允许时记录当前 session 的 `pg_backend_memory_contexts` retained/used
  bytes；权限不足时显式标记 unavailable。
- CPU：stock PostgreSQL 不提供可移植的 per-query server CPU 指标，未安装
  `pg_stat_kcache` 也不依赖 Docker/OS 特权；报告必须标记 server CPU unavailable，
  不用 Python 客户端 CPU 冒充。
- 构建与存储：记录 COPY 时间、标量过滤索引构建时间、ANALYZE 时间、
  table/index/total bytes；触发 HNSW 时另记录 ANN 构建时间与索引体积。

## 门禁与运行时边界

1. 任一 scale 的 exact P95 `> 100 ms` 才运行 HNSW 实验。
2. 未触发：报告 `keep_exact`，不创建 HNSW。
3. 触发但无任何 `ef_search` 同时通过质量、SLA 和收益门禁：报告
   `keep_exact_hnsw_not_eligible`。
4. 有候选通过：只报告 `hnsw_eligible_for_followup`，不在本 PR 开启运行时索引。
5. 真正启用前还需生产维度化 schema，workspace/visibility/active 过滤，iterative scan，
   增量写入、索引构建资源、canary 和 rollback 评审。

## 官方依据

- [pgvector README](https://github.com/pgvector/pgvector)：exact 默认、HNSW 取舍、
  `ef_search`、iterative scan 与 exact recall 比较方法。
- [PostgreSQL EXPLAIN](https://www.postgresql.org/docs/current/sql-explain.html)：
  `ANALYZE/BUFFERS/SETTINGS` 的执行与资源语义。
- [PostgreSQL pg_backend_memory_contexts](https://www.postgresql.org/docs/current/view-pg-backend-memory-contexts.html)：
  当前 backend session 的内存上下文观测边界。

## 非目标

- 不修改默认 SQLite/PostgreSQL backend 选择。
- 不在 `knowledge_index_chunks` 创建 HNSW/IVFFlat 索引。
- 不用合成向量推导真实查询分布、生产 SLA 或容量。
- 不部署，不接飞书，不合入 `main`。
