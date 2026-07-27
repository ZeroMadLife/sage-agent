# Sage Knowledge 真实语义 Provider 与 Gate v1

> 日期：2026-07-28  
> 数据集：`sage-official-agent-fullstack-v1@2026-07-27.1`  
> source commit：`cbeb08e417ef4d9b1a37bd19649a882df7a5efbf`  
> selection：[knowledge_semantic_postgres_selection_v1_2026-07-28.json](../../evals/reports/knowledge_semantic_postgres_selection_v1_2026-07-28.json)  
> final：[knowledge_semantic_postgres_final_v1_2026-07-28.json](../../evals/reports/knowledge_semantic_postgres_final_v1_2026-07-28.json)  
> candidate policy：`krp_eeaef9ce9dfa08b1`

## 结论

PR-4 已交付可运行的真实语义 Provider、固定模型版本、PostgreSQL pgvector exact 消融、分阶段
Eval 和 route-specific Gate policy v2，但 **FastEmbed candidate 暂不默认启用**。

- `dev + calibration` 的 selection 六项门禁全部通过；hybrid Recall@10 从 `0.940` 提升到
  `1.000`，语义改写子集从 `0.917` 提升到 `1.000`。
- 冻结 test 上，hybrid Recall@10 从 `0.889` 提升到 `0.944`，MRR 从 `0.683` 提升到
  `0.771`，Abstain F1 从 `0.667` 提升到 `0.800`，citation support 保持 `1.000`。
- 但冻结 test 的 `semantic_paraphrase` case 数为 `0`，无法验证预先冻结的“语义改写至少
  `+5pp`”门槛。报告对此 fail closed，没有修改 test、降低门槛或把 calibration 指标包装成
  held-out 指标。

因此运行时默认仍是显式配置的 `hashing`；`fastembed` 和 candidate policy 可审阅、可复现、可
手动启用实验，但不是生产默认。后续必须通过新的、事先冻结且含语义改写覆盖的 dataset revision
重新验收，不能回填当前 test。

## Provider 选择与版本边界

候选为 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`：384 维、约 0.22 GB、支持
多语言且不要求 query/passage 前缀，适配当前对称的 `embed(text)` 合同。FastEmbed 使用本地
ONNX Runtime，不产生外部逐请求费用。模型仓库和运行时同时固定：

- ONNX repository：`qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q`；
- snapshot：`faf4aa4225822f3bc6376869cb1164e8e3feedd0`；
- runtime：`fastembed==0.8.0`，mean-pooling 行为进入 `model_revision`；
- cache：显式持久目录，下载按 commit SHA；离线模式只读本地 cache；
- 失败策略：初始化、维度、数量或向量异常都显式报错，绝不静默退回 Hashing。

模型信息以 [FastEmbed supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/)
和 [固定 Hugging Face snapshot](https://huggingface.co/qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q/tree/faf4aa4225822f3bc6376869cb1164e8e3feedd0)
为依据。现有 OpenAI-compatible adapter 保留；本轮因没有可用且授权的 embedding endpoint，选择
可离线复现的本地语义基线，而不是借用图像 API 密钥或伪造远程成本。

## 防泄漏协议

评测脚本固定两个阶段：

1. `selection` 只评估 `dev=40 + calibration=20`，`test=0`；Provider、三路 Gate 阈值和启用
   决策只来自该阶段。
2. `final` 只加载 selection 产出的固定阈值，评估 `calibration=20 + test=20`；报告中最终门禁
   只聚合 `test`。final 不重新校准，输出文件存在时拒绝覆盖。

查询不预缓存，P95 包含单条查询 embedding、PostgreSQL 连接和 exact retrieval；文档 embedding
批量准备时间单独记录。报告绑定 clean source、dataset/cases hash、Provider revision 和
deterministic digest。

## Selection 结果

60 条 selection case 中有 58 条可回答、2 条无答案；9 份官方资料形成 49 chunks。

| 路线 | Hashing Recall / MRR / NDCG | Semantic Recall / MRR / NDCG | Semantic P95 |
| --- | --- | --- | ---: |
| sparse | 0.957 / 0.648 / 0.712 | 0.957 / 0.648 / 0.712 | 6.387 ms |
| dense exact | 0.638 / 0.384 / 0.434 | 0.966 / 0.793 / 0.826 | 112.478 ms |
| hybrid RRF | 0.940 / 0.687 / 0.735 | 1.000 / 0.791 / 0.831 | 13.931 ms |

激活判断只针对默认检索路线 hybrid：

| 门禁 | 冻结条件 | selection 结果 |
| --- | ---: | ---: |
| semantic paraphrase Recall@10 | delta >= +0.05 | +0.083333，pass |
| overall Recall@10 | delta >= -0.01 | +0.060345，pass |
| Abstain F1 | delta >= -0.05 | -0.047619，pass |
| citation support | delta >= -0.001 | 0，pass |
| P95 | <= 100 ms | 13.931 ms，pass |
| estimated cost | <= $0.01 | $0，pass |

dense-only P95 超过 100 ms，但它是独立消融，不是默认路线；hybrid 的 P95 门禁通过。该延迟只对应
本机 49 chunks，不能解释为生产 SLA，也不能触发 HNSW。

## 冻结 Test 结果

下面只取 20 条 test，不混入 calibration；18 条可回答、2 条无答案。Gate 使用 selection 阈值：

| hybrid 指标 | Hashing | Semantic | delta |
| --- | ---: | ---: | ---: |
| Recall@10 | 0.889 | 0.944 | +0.055556 |
| MRR | 0.683 | 0.771 | +0.088294 |
| NDCG@10 | 0.701 | 0.782 | +0.081320 |
| Answerable Recall | 0.889 | 0.944 | +0.055556 |
| Abstain F1 | 0.667 | 0.800 | +0.133333 |
| citation support | 1.000 | 1.000 | 0 |

test 查询 P95 为 `14.674 ms`，费用为 `0`。semantic candidate 将 false rejection 从 2 降到 1，
没有引入 false acceptance；但 semantic-paraphrase coverage 为 0，所以总门禁仍失败。

## Gate Policy v2

selection 生成的 candidate policy 保存为
`evals/policies/knowledge_relevance_fastembed_candidate_v1.json`：

- sparse：`0.10041373`；
- dense：`0.49292439222335815`；
- hybrid RRF：`0.0315136476426799`。

v2 按 retrieval route 使用对应阈值，修复旧 policy 只能表达 sparse/dense、却无法准确表达 hybrid
RRF Gate 的契约缺口。v1 policy 的序列化字段和 `policy_id` 保持兼容，已提交旧 policy 回归测试。

## 复现与限制

```bash
PYTHONPATH=packages/sage_harness .venv/bin/python -m \
  scripts.evaluate_knowledge_semantic \
  --stage selection --backend postgres \
  --postgres-dsn "$KNOWLEDGE_POSTGRES_DSN" \
  --output evals/reports/knowledge_semantic_postgres_selection_v1_2026-07-28.json
```

final 需额外传 `--selection-report`，并写入新的 final 路径。selection SHA256 为
`a17832e072028cd1bc2dc433d87c519d28972c6558177616d4977a5bfff25279`；纠正为 test-only 聚合后的
final SHA256 为 `4363b364840a96ac9b6e3c9e673588d9af82cae9e918fdbba449d057ed739752`。

可以写：实现并评测固定 snapshot 的多语言 ONNX semantic Provider；在冻结 test 上将 PostgreSQL
hybrid Recall@10 从 0.889 提升到 0.944、MRR 从 0.683 提升到 0.771，同时保持 citation support
1.0，并因 held-out 语义改写覆盖缺失而拒绝默认启用。

不能写：线上准确率 94.4%；语义改写已通过 held-out 验证；生成回答质量已评测；本地 49 chunks
延迟是生产 SLA；HNSW 已有必要。本 PR 没有部署、没有飞书、没有合入 `main`。
