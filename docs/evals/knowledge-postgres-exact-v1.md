# Sage Knowledge PostgreSQL 精确检索基准 v1

> 日期：2026-07-27
> 数据集：`sage-official-agent-fullstack-v1@2026-07-27.1`
> clean source：`b3c3329dd8e0e073c6704f8709e63b44b89ed53b`
> 报告：[knowledge_postgres_exact_v1_2026-07-27.json](../../evals/reports/knowledge_postgres_exact_v1_2026-07-27.json)
> deterministic digest：`sha256:d761f019304179e1731fede72a22ac059db3c172c1af89761e62d0f6e503042c`

## 实验问题

PR-3 只回答 PostgreSQL 工程迁移问题，不提前回答真实语义召回和 HNSW 问题：

1. SQLite/Git 继续作为 canonical truth 时，PostgreSQL 能否成为可重建的检索投影？
2. GIN + `ts_rank_cd` sparse、pgvector exact dense 和 RRF hybrid 能否保持稳定引用与过滤合同？
3. 相同冻结数据集和 Hashing Provider 下，相对 SQLite 的 Recall@10 下降是否不超过 2 个百分点？

## 固定输入与实现边界

- Corpus 为 9 个审批快照，摄取后形成 49 个 chunks；Eval 为 80 条，
  `dev/calibration/test=40/20/20`，其中 76 条可回答、4 条无答案。
- Provider 为 `sage.hashing@1.0.0`、256 维，`supports_semantic_recall=false`；dense 指标只是跨后端
  一致性对照，不能写成语义检索效果。
- sparse 使用应用层 CJK bigram/代码 token、PostgreSQL GIN、`websearch_to_tsquery` 和
  `ts_rank_cd(..., 33)`；它不是 BM25。
- dense 使用 pgvector cosine exact scan；schema 中没有 HNSW/IVFFlat。hybrid 复用 RRF，并保持
  Hashing dense 只重排 sparse candidates 的既有约束。
- 检索先过滤 workspace、visibility、source/revision、Provider model/revision/dimensions，再排序。
  SQLite proposal/revision/graph 不迁移，PostgreSQL 投影可按 workspace 丢弃重建。

## 全量 80 题结果

Retrieval/Ranking 只在 76 条可回答 case 上取平均。括号内为相对 SQLite baseline 的变化。

| 路线 | Recall@10 | MRR | NDCG@10 | Candidate Recall@50 | P50 / P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| PostgreSQL sparse | 0.954（+0.000） | 0.672（-0.049） | 0.722（-0.047） | 0.974 | 3.520 / 5.606 ms |
| pgvector exact Hashing | 0.612（+0.000） | 0.365（+0.000） | 0.412（+0.000） | 0.855 | 7.643 / 13.402 ms |
| PostgreSQL hybrid RRF | 0.928（-0.020） | 0.686（+0.009） | 0.727（-0.007） | 0.974 | 6.670 / 9.936 ms |

三条路线都通过 `Recall@10 delta >= -0.02` 门禁。sparse 的召回与 SQLite 相同，但不同 ranking
函数让 MRR/NDCG 分别下降 4.9/4.7 个百分点；这说明“命中标准 passage”与“把它排到更前”是两个
问题。hybrid 的 Recall 下降 1.974 个百分点，刚好在容忍边界内，不能描述为无损迁移；它的 MRR
略升而 NDCG 略降，体现 RRF 对首个相关结果和多 passage 排序的影响不同。

formal report 记录的本机延迟只对应 49 chunks，包含 PostgreSQL 连接与 SQL 执行成本。它既不是
生产 SLA，也不能用于判断何时需要 ANN；规模触发条件留给 PR-8。

## 冻结 test 结果

| 路线 | Recall@10 | MRR | NDCG@10 | Answerable F1 | Abstain F1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| PostgreSQL sparse | 0.944 | 0.752 | 0.755 | 0.714 | 0.333 |
| pgvector exact Hashing | 0.528 | 0.304 | 0.340 | 0.919 | 0.000 |
| PostgreSQL hybrid RRF | 0.889 | 0.683 | 0.701 | 0.941 | 0.667 |

test 的 sparse 候选召回为 1.0，但 18 条可回答题中有 8 条被 Gate 拒绝；hybrid 有 2 条 false
rejection。calibration 仅含 18 条可回答、2 条无答案，所选阈值在 split 间不稳定。PR-4 必须在真实
语义 Provider 的分数分布上重新校准 Gate，不能直接沿用当前 Hashing/FTS 阈值。

## Failure 与 case 退化

| 路线 | Retrieval | Ranking | False rejection | False acceptance | System/Citation |
| --- | ---: | ---: | ---: | ---: | ---: |
| PostgreSQL sparse | 2 | 0 | 19 | 0 | 0 |
| pgvector exact Hashing | 9 | 19 | 1 | 3 | 0 |
| PostgreSQL hybrid | 2 | 1 | 4 | 1 | 0 |

- sparse 相对 SQLite 有 `ragv1-test-07a` 的单 case passage recall 下降 0.5，但其他 case 补偿后整体
  Recall 持平。
- hybrid 的 `ragv1-calibration-02a` 从无失败变为 ranking failure，另有 `ragv1-test-07a` passage
  recall 下降 0.5；整体 Recall 因此下降 1.974 个百分点。
- dense-only 与 SQLite 的 Recall/MRR/NDCG 完全一致，证明 pgvector exact 与 Python cosine 在当前
  Hashing 输入上的排序合同一致；低指标仍来自非语义 Provider，不是 PostgreSQL 丢失向量召回。
- 三路均无 ingestion、context、citation 或 system failure。481/766/576 条 route evidence 的
  citation support 与 source revision validity 都为 1.0。

## 恢复、存储与运行边界

- 真实 PostgreSQL 集成测试覆盖 GIN 且无 ANN index、source/visibility/revision filter、稳定
  chunk/citation ID、重复 force rebuild、失败 revision 原子性、维度漂移重建和恢复。
- pooled connection 每次成功或失败都结束 transaction，测试确认没有 `idle in transaction`。
- `--force` 先幂等建 schema、只删除目标 workspace，再单次回填，避免真实 Provider 重复计算
  embedding；错误摘要有界且不记录 DSN。
- 49 chunks 的 workspace row bytes 为 144152；共享 chunks relation 总量为 540672 bytes，GIN 为
  114688 bytes。共享表/索引大小不能冒充单 workspace 成本。

## 复现

```bash
PYTHONPATH=packages/sage_harness:. .venv/bin/python \
  scripts/evaluate_knowledge_postgres_exact.py \
  --postgres-dsn "$KNOWLEDGE_POSTGRES_DSN" \
  --output evals/reports/knowledge_postgres_exact_v1_2026-07-27.json
```

- 正式报告绑定 clean source SHA、SQLite baseline SHA256、dataset/cases hash、Provider 与 schema
  revision；dirty source 默认拒绝。
- 两次独立 workspace 运行得到相同 deterministic digest；评测后 PostgreSQL 残留 workspace 为 0。
- CLI 返回码 2 表示 Recall regression gate 未通过，不会只生成报告后仍伪装成功。

## 可以写与不能写

可以写：设计 SQLite/Git canonical truth + PostgreSQL 可重建检索投影；实现 GIN + `ts_rank_cd`、
pgvector exact、RRF、稳定引用、过滤与迁移恢复；在 9 份官方语料、80 条冻结 Eval 上，PostgreSQL
sparse Recall@10 0.954 与 SQLite 持平，hybrid Recall@10 0.928、MRR 0.686，并用逐 case 门禁揭示
1.974 个百分点召回下降和 Gate 分布漂移。

不能写：线上准确率 95.4%；PostgreSQL 已替代全部 canonical 数据；Hashing 是真实语义模型；
`ts_rank_cd` 是 BM25；拒答已成熟；HNSW 已验证或 49 chunks 延迟代表生产规模。本 PR 没有部署、
没有接飞书、没有合入 `main`。
