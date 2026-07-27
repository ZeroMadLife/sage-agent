# Sage Knowledge 检索失败可观测性 v1

> 日期：2026-07-28
> 数据集：`sage-official-agent-fullstack-v1@2026-07-27.1`
> clean source：`f800f6fb65a25599abd08c1258643dc06c2bbc9c`
> 报告：[knowledge_retrieval_observability_v1_2026-07-28.json](../../evals/reports/knowledge_retrieval_observability_v1_2026-07-28.json)
> 报告 SHA256：`6c6eccac4571e11697e1585f36358cf57186af3ed71faf42148306da444a9c64`

## 结论

PR-5A 在 SQLite 和 PostgreSQL 检索后端建立了默认关闭、隐私受限、写失败不影响主链路的
`knowledge_retrieval_runs`，同时为每条 Eval case 增加分层 trace。它只解决“失败发生在哪一层”，
不在本阶段改写查询、扩大 Top-K 或改变排序。

- 新报告三条 route 的 Recall@10、MRR、NDCG@10 与 PR-2 baseline 逐项完全相同；可观测性没有
  改变返回 hit 和排序。
- 80 case x 3 route 共 240 个 route-case，41 个失败行全部有且只有一个 `failure_type`，trace 与
  `primary_failure` 100% 一致。
- 运行时不保存原始 query、rewrite、chunk 正文、excerpt、source path、密钥或异常消息；query 与
  rewrite 只保存带部署密钥的 HMAC-SHA256 指纹。
- SQLite schema 从 9 迁移到 10；PostgreSQL 记录独立 migration revision
  `20260728_rag_retrieval_trace_v1`，兼容 PR-3 已存在的空表。
- 功能默认关闭，本 PR 没有部署、没有接飞书、没有合入 `main`。

## 为什么运行时与 Eval 使用两套失败词汇

运行时没有相关性金标，不能知道候选是否为“正确答案”，也不能把 Gate 拒绝直接称为误拒。因此
运行时只记录可从系统状态直接观察到的类型：

| 运行时 `failure_type` | 可验证条件 |
| --- | --- |
| `ingestion` | 当前 Provider revision 下没有 active chunk |
| `retrieval` | 已有 active chunk，但 sparse/dense/RRF 没有候选 |
| `gate_rejected` | 有候选，配置的 Gate 没有接受任何结果 |
| `system` | 检索抛出异常；只保存异常类名 |
| `none` | 返回至少一个结果，或当前没有可在线判定的失败 |

只有版本化 Eval 拥有 answerable、required passages、forbidden claims 和 citation 金标，才能进一步
区分 `ranking`、`context`、`false_rejection`、`false_acceptance`、`grounding` 与 `citation`。这也是
为什么线上 `gate_rejected` 不能直接包装成 `false_rejection`。

## `retrieval_runs` 契约

| 字段 | 含义与边界 |
| --- | --- |
| `run_id` | 随机运行 ID，不编码 query 或正文 |
| `query_hash` / `rewrite_hash` | `hmac-sha256:<digest>`；rewrite 未发生时为 `NULL` |
| `round_index` | PR-5A 固定为 1；为 PR-5B 的最多两轮状态机预留 |
| `retrieval_mode` / Provider revision / corpus revision | 绑定 route 与可复现输入 |
| `top_k` / `candidate_limit` | 请求返回数和后端候选预算 |
| `candidate_count` / `returned_count` | RRF 前完整候选数和 Gate 后返回数 |
| `candidates_json` | 默认最多 50、硬上限 200 条 chunk ID、rank、RRF/sparse/dense 分数和 selected 标记 |
| `result_coverage` | `returned_count / top_k`，只是结果槽位占用率，不是金标证据覆盖率 |
| `gate_decision` | `not_configured`、`not_evaluated`、`accepted` 或 `rejected` |
| `latency_ms` | 核心检索耗时；不包含异步运维或本次同步 trace 写入耗时 |
| `failure_type` / `error_type` | 可在线判定的主要失败；异常只保存类名 |

PostgreSQL 旧字段 `failure_layer` 暂时写入与 `failure_type` 相同的值，作为向后兼容别名；新代码以
`failure_type` 为准。候选 JSON 有固定上限，避免一次异常 query 产生无界 trace。

## 隐私与故障策略

`KNOWLEDGE_RETRIEVAL_OBSERVABILITY_ENABLED=false` 是默认值。开启时必须提供至少 32 bytes 的
`KNOWLEDGE_RETRIEVAL_OBSERVABILITY_HMAC_KEY`；短密钥在应用构造阶段失败，密钥字段使用
`repr=False`。普通 SHA-256 无法抵抗低熵 query 的离线字典枚举，带密钥 HMAC 才允许在不知道
query 原文的前提下聚合同一查询。

trace 写入采用 fail-open：SQLite/PostgreSQL 写失败不会改变原检索结果，也不会覆盖原检索异常；
日志只写异常类名，不写异常消息。单测用包含敏感标记的 query、正文、Provider 异常和 trace 写入
异常做反向断言，确认它们没有出现在持久化行或日志。

正式 Eval JSON 仍按既有数据集契约保存官方 case query，便于逐题复核；它是受版本控制的评测工件，
不是运行时 `retrieval_runs`，也不代表任意用户 query 可以落盘。

当前没有部署，所以没有伪造线上 retention SLA。正式长期运行前仍需补按 workspace/time 的保留
窗口、删除作业和表容量告警。HMAC 允许同一密钥周期内关联相同 query，这本身是受控的可链接
元数据；换钥会切断跨周期聚合，旧指纹也无法自动重算。

## 80-case 结果

检索指标与 PR-2 SQLite baseline 完全一致：

| route | Recall@10 | MRR | NDCG@10 | Candidate Recall@50 |
| --- | ---: | ---: | ---: | ---: |
| sparse | 0.954 | 0.721 | 0.769 | 0.974 |
| Hashing dense | 0.612 | 0.365 | 0.412 | 0.842 |
| hybrid RRF | 0.947 | 0.677 | 0.734 | 0.974 |

失败数按 route-case 统计，不是 41 个独立用户问题：

| 主要失败 | sparse | dense | hybrid | 合计 |
| --- | ---: | ---: | ---: | ---: |
| retrieval | 2 | 10 | 2 | 14 |
| ranking | 0 | 18 | 0 | 18 |
| false rejection | 0 | 1 | 3 | 4 |
| false acceptance | 1 | 3 | 1 | 5 |
| ingestion/context/grounding/citation/system | 0 | 0 | 0 | 0 |

这些数字说明 Hashing dense 的主要问题不只有“没召回”：18 行是候选中已有相关 passage，但没有
进入 Top-10 的排序失败。hybrid 的主要剩余问题是 3 行 Eval 金标下的 Gate 误拒。PR-5B 应针对
retrieval failure 子集做有限恢复，不能通过降低 Gate 阈值掩盖 false rejection。

正式报告中的 P50/P95 仍是本机 49 chunks 的 Eval 延迟，不是可观测性开销实验，也不是生产 SLA；
本 PR 只用结果完全等价测试证明排序不变，不宣称 trace overhead 为零。

## 验证证据

- Knowledge + API：`158 passed, 6 skipped`；PostgreSQL 用独立命令补跑。
- 真实 `pgvector/pg16`：`11 passed`，覆盖 schema migration、JSONB trace、无 ANN index、清理和
  SQLite 观测回归。
- 定向配置/API/SQLite/Eval：`63 passed`。
- Ruff lint 全量通过；mypy：`204 source files` 无问题。
- 正式 Eval：clean source、80 case、三 route、deterministic digest
  `sha256:ad1f925eb5d53846255517d6e1a58bd4de775ba6c08a894b5cf5a52afc3010e7`。

## 复现

```bash
PYTHONPATH="$PWD" .venv/bin/python \
  scripts/evaluate_knowledge_sqlite_baseline.py \
  --output evals/reports/knowledge_retrieval_observability_v1_2026-07-28.json
```

运行时开启示例只展示变量名，不提供或提交真实密钥：

```bash
KNOWLEDGE_RETRIEVAL_OBSERVABILITY_ENABLED=true
KNOWLEDGE_RETRIEVAL_OBSERVABILITY_HMAC_KEY=<at-least-32-random-bytes>
KNOWLEDGE_RETRIEVAL_OBSERVABILITY_CANDIDATE_LIMIT=50
```

可以写：为 SQLite/PostgreSQL RAG 建立隐私受限的 `retrieval_runs` 与 Eval 分层失败 trace；在 9 份
官方语料、80 条冻结 Eval、三条 route 上保持 Recall/MRR/NDCG 完全不变，并将 41 个失败 route-case
100% 归入唯一主要失败类型。

不能写：线上故障定位率 100%；41 个独立用户问题；HMAC 后完全没有隐私风险；`result_coverage` 是
答案覆盖率；已实现自动恢复；观测无性能开销；已部署生产。
