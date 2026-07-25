# 16 - RAG Benchmark v2：指标必须绑定当前语料与 Provider

> Last verified against: `codex/harness-evidence-v2@a03802d` (2026-07-25)

RAG 评测最容易犯的错误，是拿旧语料、旧映射和旧 Provider 产生的数字描述当前系统。
Benchmark v2 首先解决证据可复现，再比较检索策略。

![RAG 全链路评测](assets/16-rag-eval-benchmark.png)

## RAG 在 Sage 中解决什么问题

Sage 的 Knowledge 不是把全部文档塞进 prompt，而是把来源 revision、chunk、检索、引用和
回答组装拆成可追溯链路：

```text
source snapshot
  -> parse + section-preserving chunk
  -> FTS5 BM25 / pluggable embedding
  -> RRF fusion
  -> token-bounded context
  -> citation_id + source revision
```

HashingEmbedding 是可离线运行的确定性基线，不支持语义召回。只有 Provider 明确声明
`supports_semantic_recall=true`，才能把 dense 路径称为语义检索。

## v1 为什么不能继续作为简历证据

旧评测只有 50 条文档级 `relevant_sources`，并且从 V6 结构映射到 V7 时只剩 35 条可用。
旧报告还引用了不在当前分支中的 Provider 脚本和缓存路径。

这些数字可以作为历史实验记录，但不能证明当前 17 份语料、当前 chunk 和当前检索代码的
效果。Benchmark v2 因而 fail closed：数据集、文件集合或任一语料 SHA 漂移，运行器直接
终止，不静默重算标签。

## 200 条固定查询

| 类别 | 数量 | 主要验证 |
| --- | ---: | --- |
| 旧查询迁移 | 50 | 保留历史 smoke 回归 |
| 真实用户式问题 | 60 | 避免标题复制式查询 |
| 改写与中英文混合 | 30 | 验证语义召回 |
| hard negative | 20 | 区分相似概念 |
| 多文档问题 | 20 | 验证跨来源召回 |
| 无答案问题 | 20 | 验证 abstention |

每条记录保存 split、provenance、section 级 graded qrels、required claims 和 forbidden claims。
本阶段只评 retrieval；claims 为后续 generation evaluation 预留，不能提前当成回答正确率。

## 指标口径

- `Recall@10`：相关 passage 有多少被找回；
- `Precision@10`：前 10 个结果中有多少相关；
- `MRR`：第一个相关结果是否足够靠前；
- `NDCG@10`：结合位置与 1-3 级相关性的排序质量；
- `HitRate@10`：可回答查询是否至少命中一次；
- `unanswerable_accuracy`：无答案查询是否返回空结果；
- P50/P95：同一机器上 search 调用耗时，不包含首次远程 embedding 预热。

## 2026-07-25 clean baseline

固定输入为 17 份 Markdown、915 active chunks、180 条可回答查询和 20 条无答案查询。

| 配置 | Recall@10 | MRR | NDCG@10 | HitRate@10 | P50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| FTS5 + Hashing + RRF | 0.569 | 0.389 | 0.428 | 0.600 | 28.9 ms |
| FTS5 + text-embedding-v3 + RRF | 0.819 | 0.666 | 0.692 | 0.856 | 165.3 ms |

语义双路相对 Hashing 基线：Recall@10 提升 43.9%，MRR 提升 71.1%，NDCG@10 提升
61.8%。30 条改写题的 Recall@10 从 0.400 提升到 0.867，说明提升主要来自真实语义能力，
不是把确定性哈希重新命名。

## 失败结果同样是结论

两个配置的无答案准确率均为 0。当前 search 固定返回 top-k，尚未实现校准过的 abstention。

因此项目可以写“建立了无答案评测并识别拒答缺口”，不能写“RAG 已可靠避免无依据回答”。
下一步需要只在 dev split 上校准阈值，再用 test split 验收，不能反复看 test 结果调参。

多文档 Recall@10 也只有 0.525，说明单次 section 级检索不能替代 query decomposition 或
multi-hop 组装。

## 复现

```bash
# 无远程凭据的离线基线
python scripts/benchmark_knowledge_retrieval_v2.py \
  --top-k 10 \
  --output tmp/knowledge-benchmark-v2-hashing.json

# 显式注入真实 Provider
DASHSCOPE_API_KEY=... python scripts/benchmark_knowledge_retrieval_v2.py \
  --provider-factory scripts.benchmark_providers.dashscope:create_provider \
  --top-k 10 \
  --output tmp/knowledge-benchmark-v2-dashscope.json
```

数据清单：`evals/knowledge_benchmark_v2_manifest.json`。机器摘要：
`evals/reports/knowledge_benchmark_v2_2026-07-25.json`。完整逐 case 报告由命令生成，不把
约 1.8 MB 运行产物提交到仓库。

## 当前边界

- 数据集来自项目维护者构造和复核，不是独立外部用户流量；
- 当前数字只证明 retrieval，不证明最终回答 faithful 或 complete；
- 没有可靠 abstention，无答案问题仍可能召回相似但无关内容；
- 真实语义 Provider P50 高于离线基线，质量与延迟需要一起展示；
- 语料规模是 17 文件 / 915 chunks，不得扩写为企业级知识库规模。

## 面试里可以这样收束

Sage 不再引用旧语料产生的漂亮数字，而是冻结 200 条分层查询、section 级 qrels 和 17 份
语料 SHA，通过同一运行器比较离线 Hashing 与真实语义 Provider。语义双路将 Recall@10
从 0.569 提升到 0.819；同时 20 条无答案题暴露出 abstention 为 0，下一阶段围绕阈值校准
和回答生成评测继续闭环。
