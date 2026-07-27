# Sage Knowledge SQLite 分层 Baseline v1

> 日期：2026-07-27
> 数据集：`sage-official-agent-fullstack-v1@2026-07-27.1`
> clean source：`466079959e1c569b1b886b47a8abc82ba152582b`
> 报告：[knowledge_sqlite_layered_v1_2026-07-27.json](../../evals/reports/knowledge_sqlite_layered_v1_2026-07-27.json)
> deterministic digest：`sha256:e6558bb360858ec3b5501d56a180da177f1c4d002392edacbe90435476678e1b`

## 实验问题

这次不先问“怎样把分数调高”，而是回答三个工程问题：

1. 同一份官方语料和 80 条冻结案例，SQLite sparse、Hashing dense 和 hybrid RRF 分别失败在哪一层？
2. Gate 阈值能否只用 calibration 选择，并把 false rejection/acceptance 与检索失败分开？
3. 报告能否绑定输入 hash、代码 SHA、Provider、index revision，并在重复运行时得到同一摘要？

## 固定输入

- Corpus：9 个审批快照、4 个官方项目，摄取后形成 49 个 chunks；index error 为 0。
- Eval：80 条，`dev/calibration/test=40/20/20`；共 76 条可回答、4 条无答案。
- Provider：`sage.hashing@1.0.0`，256 维，`supports_semantic_recall=false`。
- 参数：`candidate_k=50`、`top_k=10`、context budget 3000；Gate 最低 answerable recall 目标 0.90。
- calibration 只有 18 条可回答、2 条无答案；test 只有 18 条可回答、2 条无答案，因此 Gate
  指标只用于当前受控集合，不外推线上分布。

## 全量 80 题结果

Retrieval/Ranking 指标只在 76 条可回答 case 上取平均；Gate 与 failure count 使用全部 80 条。

| 路线 | Recall@10 | MRR | NDCG@10 | Candidate Recall@50 | P50 / P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| SQLite FTS5 sparse-only | 0.954 | 0.721 | 0.769 | 0.974 | 0.689 / 0.849 ms |
| Hashing dense-only | 0.612 | 0.365 | 0.412 | 0.842 | 2.124 / 2.534 ms |
| FTS5 + constrained Hashing + RRF | 0.947 | 0.677 | 0.734 | 0.974 | 2.314 / 2.524 ms |

当前官方语料的问题集中在 API、配置、类名、代码标识符和精确事实。sparse-only 在这类集合上
最好；Hashing dense-only 比 sparse Recall@10 低 34.2 个百分点，NDCG@10 低 35.7 个百分点。
hybrid 相比 sparse Recall@10 低 0.7 个百分点，NDCG@10 低 3.5 个百分点。这里证明的是
**确定性特征哈希不应冒充语义召回**，不是“向量检索无效”。PR-4 必须用真实语义 Provider 在
同一数据集上重跑，才能判断 semantic dense 对 paraphrase 的净收益。

## 冻结 test 结果

| 路线 | Recall@10 | MRR | NDCG@10 | Answerable F1 | Abstain F1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| sparse-only | 0.972 | 0.769 | 0.803 | 1.000 | 1.000 |
| Hashing dense-only | 0.528 | 0.304 | 0.340 | 0.919 | 0.000 |
| hybrid RRF | 0.917 | 0.645 | 0.694 | 0.971 | 0.800 |

sparse 在这 20 条 test 上恰好 20/20 没有 primary failure，但无答案题只有 2 条，不能把
Abstain F1=1.0 写成可靠拒答率。hybrid 的 1 条 test 失败是 Gate 对 `use_cache=False` 问题的
false rejection；dense-only 有 2 retrieval、6 ranking、1 false rejection 和 2 false acceptance。

## Failure taxonomy 的实际价值

全量 primary failure 分布：

| 路线 | Retrieval | Ranking | False rejection | False acceptance | 其他已评层 |
| --- | ---: | ---: | ---: | ---: | ---: |
| sparse-only | 2 | 0 | 0 | 1 | 0 |
| Hashing dense-only | 10 | 18 | 1 | 3 | 0 |
| hybrid RRF | 2 | 0 | 3 | 1 | 0 |

- sparse/hybrid 的两条 retrieval failure 指向 HNSW + filtering 的改写问题；候选池内也没有标准
  passage，说明扩大当前 Top-K 不能解决，后续应优先 query rewrite 或真实语义补召回。
- hybrid 的 3 条 false rejection 中，两条是安装/最小图代码问题，一条是 FastAPI
  `use_cache=False`；标准 passage 已在 Top-K，问题属于 Gate，而不是召回。
- dense-only 的 18 条 ranking failure 说明标准 passage 已进入 Recall@50 候选池但排不到前十；
  这类失败未来应由真实语义表示或 reranker 消融处理，而不是继续扩大 Top-K。
- 没有 ingestion/context/citation/system failure。每个失败 case 都在 JSON 中保留 query、route score、
  passage rank 和 primary layer，可直接回放。

## Generation 与 Citation 边界

本阶段没有运行生成模型。deterministic extractive proxy 只拼接已检索 excerpt，因此 608/765/595
条 route evidence 的 citation support 和 revision validity 都为 1.0；这验证的是证据链合同，不是
回答质量。

required claims 主要是中文，官方快照主要是英文，三路 claim token recall 只有 0.101/0.076/0.096。
它只说明直接抽取英文无法覆盖中文答案表述，是跨语言 completeness 下界。报告没有用这个值制造
grounding failure，也没有使用 LLM judge。真实 required/forbidden claims、faithfulness 和生成拒答
应在接入真实 Provider 后单独评估。

## 复现与门禁

```bash
PYTHONPATH=packages/sage_harness:. .venv/bin/python \
  scripts/evaluate_knowledge_sqlite_baseline.py \
  --output evals/reports/knowledge_sqlite_layered_v1_2026-07-27.json
```

- 正式 CLI 默认拒绝 dirty source；本报告记录 `source.dirty=false`。
- 两次独立临时建库得到相同 deterministic digest。
- 为修复临时 page revision UUID 导致的同分漂移，FTS、dense 和 RRF 统一使用 source path、source
  revision、ordinal、content hash 排序，chunk ID 只作最终兜底。
- 本机 P50/P95 只对应 49 chunks，不是规模或 SLA 结论；HNSW 仍必须等 PR-8 规模基准触发。

## 可以写与不能写

可以写：建立 9 份官方快照、80 条分层 Eval 和三路 SQLite 消融；报告绑定 clean SHA/hash，逐 case
定位 retrieval/ranking/Gate/citation；在当前受控集合中 sparse Recall@10 为 0.954、NDCG@10 为
0.769，并用反证确认 Hashing 不具备语义召回价值。

不能写：线上 RAG 准确率 95.4%；拒答 100%；生成回答 fully grounded；HNSW 已验证；Hashing 是
生产语义模型。PR-2 没有部署、没有接飞书、没有合入 `main`。
