# Knowledge Abstention 与 Relation Benchmark v1

## 1. 结论

本轮解决两个不同问题：RRF 只有相对排序、无法判断“是否有答案”；普通 sparse/dense 检索
只找内容相似片段、无法利用页面之间的显式关系。

- Abstention：在 dev split 校准绝对分数 gate，并绑定 corpus/provider/top-k；
- Relation：从通过 gate 的 seed 出发，只沿带原文 citation 的显式 `WIKILINK` 做有界一跳扩展。

## 2. 固定证据

- Passage 数据：`evals/knowledge_benchmark_v2.jsonl`，manifest revision `2026-07-26.1`；
- Relation 数据：`evals/knowledge_relation_benchmark_v1.jsonl`，14 条受控查询；
- Source commit：`721f9cfce2a10fa7ae483972c8f92fcec3fad47c`；
- Provider：`sage.hashing@1.0.0`，不支持真实语义召回；
- Policy：`krp_ce06e627099226df`；
- 两次运行均记录 `source_dirty=false`。

机器报告：

- `evals/reports/knowledge_abstention_v1_2026-07-26.json`；
- `evals/reports/knowledge_relation_v1_2026-07-26.json`。

## 3. Abstention 结果

阈值只使用 90 条 dev 查询选择；60 条 test 查询不参与调参，其中 50 条可回答、10 条无答案。

| Test 配置 | Recall@10 | MRR | NDCG@10 | 无答案准确率 |
| --- | ---: | ---: | ---: | ---: |
| 无 gate | 0.660 | 0.444 | 0.484 | 0.00 |
| 校准 gate | 0.620 | 0.451 | 0.482 | 0.50 |

无答案准确率提升 50 个百分点，同时 Recall@10 下降 4 个百分点。这是 risk/coverage 取舍，
不是无代价优化。当前 10 条 test negatives 太少，不能声称已经实现可靠拒答。

## 4. Relation 结果

12 条可回答查询验证目标 source 与 gold edge，2 条无答案查询只做 smoke，不用于泛化结论。

| 配置 | AllRecall@10 | Gold path recall | Path precision |
| --- | ---: | ---: | ---: |
| Hybrid seed | 0.250 | - | - |
| + evidence-bound 1-hop | 1.000 | 0.917 | 0.289 |

关系路由 P50 为 41.7 ms、P95 为 43.6 ms，仅表示本机 Hashing 小语料运行。Path precision
0.289 说明仍有非 gold 显式边被扩展；后续需要更大独立标注集验证 query-edge ranking。

## 5. 复现

```bash
python scripts/benchmark_knowledge_retrieval_v2.py \
  --top-k 10 \
  --output tmp/knowledge-v2-raw.json

python scripts/calibrate_knowledge_abstention.py \
  --report tmp/knowledge-v2-raw.json \
  --output tmp/knowledge-abstention.json \
  --policy-output tmp/knowledge-policy.json

python scripts/benchmark_knowledge_relations.py \
  --policy tmp/knowledge-policy.json \
  --output tmp/knowledge-relation.json
```

校准器默认拒绝 dirty source report。生产加载 policy 后，如果 benchmark、corpus、embedding
model/revision 或 top-k 不匹配，检索 fail closed，不静默退回未经校准的回答。

## 6. 未完成边界

- 本轮没有真实语义 Provider 凭据，尚未在当前 corpus 上重跑 embedding + gate 联合消融；
- Relation 只覆盖 Markdown/Obsidian 显式一跳，不含实体三元组、2-hop、PPR 或 community report；
- 数据集由项目维护者构造，尚未加入真实用户日志或独立标注者一致性；
- 本轮只评 retrieval/path，不评回答的 faithfulness、completeness 与 citation correctness。
