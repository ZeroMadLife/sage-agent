# Sage 长书 Query Rewrite 与 HNSW 门禁收口 v1

> 日期：2026-08-10
> clean source：`e187cea9`
> 机器摘要：[book_learning_query_rewrite_hnsw_v1_2026-08-10.json](../../evals/reports/book_learning_query_rewrite_hnsw_v1_2026-08-10.json)

## 收口结论

1. PostgreSQL `GIN + ts_rank_cd + pgvector exact cosine + RRF` 继续作为默认检索策略。
2. 预检索 Query Rewrite 有质量上界收益，但只对复杂问题保留为候选，不对所有输入强制调用。
3. 真实 2048 维豆包长书数据可以用 `halfvec` 表达式索引做 HNSW 实验，但当前 5,220 chunks 没有稳定延迟收益，运行时保持 exact。

## Query Rewrite

收据范围是 4 个人工审计的困难 case，协议明确为 `oracle_manual`，不是模型 rewrite 结果。

| 变体 | Claim Coverage | Recall@10 | MRR | NDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| original-only | 0.4444 | 0.6111 | 0.5111 | 0.3988 |
| rewrite-only | 0.8333 | 0.8889 | 1.0000 | 0.9060 |
| original + rewrite RRF | 0.8333 | 0.8889 | 1.0000 | 0.8534 |

原问题保留并与改写候选融合，相对 original-only 的 Claim Coverage 增益为 `+0.3889`；但串行
检索 P95 上界为 `20.861s`，是 `11.397s` 原问题基线的约 1.8 倍。这个结果支持“复杂度门控 +
有界候选 + 原问题不丢失”，不支持全量 rewrite 默认开启。

## HNSW

HNSW 结果使用 exact Top-10 作为 ANN oracle，并在 same corpus/filter/query 上执行两次 warmed
exact trial；最终门禁使用较低 exact P95 作为参考，同时候选超过条件才会再做第二次确认。

| 路线 | Oracle Recall@10 | P50 | P95 |
| --- | ---: | ---: | ---: |
| exact vector cosine | 1.0000 | 66.759 ms | 101.878 ms |
| halfvec HNSW ef=40 | 1.0000 | 120.630 ms | 1,387.516 ms |
| halfvec HNSW ef=80 | 1.0000 | 103.838 ms | 143.909 ms |
| halfvec HNSW ef=120 | 1.0000 | 88.831 ms | 183.491 ms |
| halfvec HNSW ef=200 | 1.0000 | 184.703 ms | 235.452 ms |

所有 HNSW 变体的 Gold Recall 仍为 `0.8833`，说明 ANN 没有改善 chunk 本身的语义覆盖；它只是在
尝试近似 exact 排序。因为没有候选满足 `P95<=100ms` 且相对 exact 至少降低 20%，结论为
`keep_exact_hnsw_not_eligible`。两次 exact trial 的 chunk oracle 一致率为 `1.0`，但 P95 仍受本机
数据库 checkpoint/连接抖动影响，不能写成生产 SLA。

## 复现

```bash
PYTHONPATH=packages/sage_harness:. .venv/bin/python \
  scripts/evaluate_book_learning_query_rewrite.py \
  --recovery-report .coding/evals/recovery-doubao.json \
  --output .coding/evals/query-rewrite-doubao-clean.json

PYTHONPATH=packages/sage_harness:. .venv/bin/python \
  scripts/benchmark_book_learning_hnsw.py \
  --skip-fetch --confirm-ephemeral-write \
  --output .coding/evals/book-hnsw-doubao-final2.json
```

DSN 和 Embedding key 从本地环境读取，不写入命令、报告或 Git；长书 TXT 只在 ignored corpus
cache 中保存。评测结束后临时 workspace 和 HNSW index 已清理。
