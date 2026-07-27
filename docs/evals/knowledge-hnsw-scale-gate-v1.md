# Sage RAG HNSW 规模门禁 v1

> 日期：2026-07-28
> clean source：`c4157e4624b58a0b2864a2ef795fd528a87a16a3`
> 报告：[knowledge_hnsw_scale_gate_v1_2026-07-28.json](../../evals/reports/knowledge_hnsw_scale_gate_v1_2026-07-28.json)
> 报告 SHA256：`23c73e74e3fdc11bf885b054d307d05e51692f70e7f840b9cbad7c9883a79e35`
> deterministic digest：`sha256:744b57797f9aa86fdd090fd7f27c9815e60299a3c7feefe7c965f0ba7f1caa5f`

## 结论

`1k/10k/100k` chunks 的 pgvector cosine exact Top-10 均完整召回生成器金标，
Recall@10 为 `1.000000`。热身后每档 64 次查询的 P95 为
`0.547 / 3.485 / 53.266 ms`，100k 仍低于预先冻结的 `100 ms`。

因此门禁结论是 `keep_exact`：`hnsw_experiment_required=false`，没有在正式
100k 报告中创建 HNSW 实验索引，也没有修改运行时 `knowledge_index_chunks`。
这不是证明“HNSW 永远无用”，而是当前合成规模与本机环境没有给出引入
ANN 复杂度的证据。

## 冻结实验契约

- 向量：384 维，cosine `<=>`；与当前 FastEmbed 语义 Provider 证据维度一致。
- 规模：`1,000 / 10,000 / 100,000`；Top-K `10`。
- 查询：32 个固定查询向量，每个查询 10 个距离严格递增的 gold neighbor。
- 延迟：1 次 warmup pass + 2 次 measured pass，每档 64 次查询。
- exact 门禁：`P95 <= 100 ms`。任一档超标才运行 HNSW。
- HNSW 条件曲线：`m=16`、`ef_construction=64`，`ef_search=40/80/120/200`。
- HNSW 后续资格：Recall@10 `>=0.98`、P95 `<=100 ms`，且相对 exact 至少降低 20%。

测试中的小规模强制触发 case 证明 HNSW 曲线路径可执行且会清理临时索引；
正式报告不降低 SLA 来强制运行 ANN。

## Exact 结果

| chunks | Recall@10 | P50 | P95 | COPY | scope index build | ANALYZE |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 1.000000 | 0.499 ms | 0.547 ms | 51.992 ms | 1.243 ms | 5.313 ms |
| 10,000 | 1.000000 | 3.237 ms | 3.485 ms | 469.885 ms | 6.390 ms | 27.613 ms |
| 100,000 | 1.000000 | 42.886 ms | 53.266 ms | 6441.078 ms | 74.220 ms | 174.108 ms |

`EXPLAIN (ANALYZE, BUFFERS, SETTINGS)` 确认三档均为
`Limit -> Sort -> Seq Scan`，且 `enable_indexscan=off`、`enable_bitmapscan=off`、
`max_parallel_workers_per_gather=0`，以 exact 串行扫描作为可比 oracle。100k 的 server
execution time 为 `54.849 ms`，shared hit/read blocks 为 `5216/14816`，没有 temp
read/write blocks。

## 存储与内存

| chunks | table bytes | total index bytes | total relation bytes | backend retained / used |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 2,097,152 | 114,688 | 2,252,800 | 2,031,624 / 1,261,752 |
| 10,000 | 16,777,216 | 843,776 | 17,661,952 | 2,095,192 / 1,338,224 |
| 100,000 | 164,102,144 | 8,052,736 | 172,228,608 | 2,095,192 / 1,323,264 |

`total index bytes` 包含基准表的 primary key 和 scope 索引，不是 HNSW 大小。
backend memory 是查询后当前 session 的 `pg_backend_memory_contexts` retained/used，
不是整个 PostgreSQL 容器的 RSS 或 peak memory。

stock PostgreSQL 没有可移植的 per-query server CPU 指标；本环境没有强制安装
`pg_stat_kcache`，也不要求特权 Docker/OS telemetry。因此报告将 server CPU 显式
标记为 unavailable，没有用 Python 进程 CPU 冒充数据库 CPU。

## 环境与安全边界

- PostgreSQL `16.14`，pgvector `0.8.4`。
- `shared_buffers=128MB`、`effective_cache_size=4GB`、`work_mem=4MB`、
  `maintenance_work_mem=64MB`。
- 基准只在随机命名的 `UNLOGGED` 表中写入 synthetic fixture，`finally` 删除。
- 评测后 `sage_hnsw_scale_bench_%` 表残留为 0；运行时
  `knowledge_index_chunks` HNSW 索引为 0。
- DSN 从环境读取，不输出到报告或日志。CLI 需显式
  `--confirm-ephemeral-write`，dirty source 默认拒绝生成正式证据。

## 复现

```bash
PYTHONPATH=packages/sage_harness:. .venv/bin/python \
  scripts/benchmark_knowledge_hnsw_scale_gate.py \
  --confirm-ephemeral-write \
  --output evals/reports/knowledge_hnsw_scale_gate_v1_2026-07-28.json
```

PostgreSQL DSN 通过 `KNOWLEDGE_POSTGRES_DSN` 或现有 `POSTGRES_*` 环境配置提供，
不要将它写进命令文档。

## 官方依据

- [pgvector README](https://github.com/pgvector/pgvector)：exact 默认、HNSW 速度/召回/
  构建成本取舍、`ef_search`、iterative scan 与 exact recall oracle。
- [PostgreSQL EXPLAIN](https://www.postgresql.org/docs/current/sql-explain.html)：
  `ANALYZE/BUFFERS/SETTINGS` 的执行语义。
- [PostgreSQL pg_backend_memory_contexts](https://www.postgresql.org/docs/current/view-pg-backend-memory-contexts.html)：
  当前 backend memory context 的可观测范围。

## 面试与简历边界

可讲：为 pgvector exact 与 HNSW 建立规模门禁，在 `1k/10k/100k`、384 维
synthetic vectors 上使用 exact Top-10 作为 recall oracle；100k Recall@10 `1.0`、
P95 `53.266 ms`，低于预先冻结的 `100 ms`，因此保持 exact，不为技术栈
展示强行启用 HNSW。

不可讲：HNSW 已上线或已完成 100k `ef_search` 曲线；100k 是真实生产
corpus/query 分布；`53.266 ms` 是线上 SLA；已测得 server CPU 或整个数据库
peak memory；HNSW 永远不需要。
