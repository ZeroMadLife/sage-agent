# Knowledge Benchmark v2

## 1. 评测目的

Benchmark v2 用当前公开学习资料验证检索层，而不是继续引用旧脚本和旧语料产生的历史数字。它回答三个问题：

1. 词法基线与真实语义 Embedding 的差距有多大；
2. 真实提问、改写、难负例和跨文档问题分别表现如何；
3. 系统是否能对语料中不存在的问题停止召回。

## 2. 固定输入

- 数据集：`evals/knowledge_benchmark_v2.jsonl`，共 200 条；
- 语料：`release/v7-beta/learning` 下 17 份 Markdown，共 915 个 active chunks；
- 清单：`evals/knowledge_benchmark_v2_manifest.json`；
- 标注：section 级、1 到 3 级相关性；
- 指标：Recall@10、Precision@10、MRR、NDCG@10、HitRate@10、无答案准确率、P50/P95。

数据集和每份语料都由 SHA-256 固定。文件集合或内容变化会直接终止评测，避免沿用失效 qrels。

## 3. 复现命令

离线 smoke：

```bash
python scripts/benchmark_knowledge_retrieval_v2.py \
  --top-k 10 \
  --output tmp/knowledge-benchmark-v2-hashing.json
```

真实语义 Provider：

```bash
DASHSCOPE_API_KEY=... python scripts/benchmark_knowledge_retrieval_v2.py \
  --provider-factory scripts.benchmark_providers.dashscope:create_provider \
  --top-k 10 \
  --output tmp/knowledge-benchmark-v2-dashscope.json
```

Provider 只读取进程环境变量，报告不保存 key、endpoint 响应或本机缓存。

## 4. 2026-07-25 基线

证据文件：`evals/reports/knowledge_benchmark_v2_2026-07-25.json`，source commit 为 `a03802d`，运行时工作区为 clean。

| 配置 | Recall@10 | MRR | NDCG@10 | HitRate@10 | P50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| FTS5 + Hashing + RRF | 0.569 | 0.389 | 0.428 | 0.600 | 28.9 ms |
| FTS5 + text-embedding-v3 + RRF | 0.819 | 0.666 | 0.692 | 0.856 | 165.3 ms |

语义双路相对离线 Hashing 基线：Recall@10 提升 43.9%，MRR 提升 71.1%，NDCG@10 提升 61.8%。其中 30 条改写题 Recall@10 从 0.400 提升到 0.867。

## 5. 当前边界

20 条无答案问题的准确率仍为 0。现有搜索固定返回 top-k，尚未基于校准集加入 abstention，因此不能声称系统已经具备可靠拒答能力。下一步应在 dev split 上校准置信阈值，再只用 test split 验收；不得用 20 条无答案题反复调参后仍把它们称为独立测试集。

当前数字只覆盖 retrieval，不等同于回答生成质量。`required_claims` 和 `forbidden_claims` 已保留在数据契约中，生成阶段评测应作为独立切片完成。
