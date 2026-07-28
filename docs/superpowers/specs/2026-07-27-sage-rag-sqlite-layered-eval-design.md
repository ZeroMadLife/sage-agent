# Sage RAG SQLite 分层 Eval Baseline 设计

> 日期：2026-07-27
> 基线：`dev/sage-v7@9fbb74d`
> 阶段：PR-2

## 1. 决策

PR-2 使用 PR-1 冻结的 `sage-official-agent-fullstack-v1@2026-07-27.1`，在同一份 SQLite
索引上分别运行 `sparse`、`dense` 和 `hybrid`。产品调用不传参数时仍保持 `hybrid`；单路模式
是后端可复用的显式检索合同，不通过修改 Provider 或伪造空候选实现。

HashingEmbeddingProvider 是确定性特征哈希。`dense` 消融会对全部 chunk 做 Python cosine
精确扫描，但结果只能写作 non-semantic hashing baseline。现有产品 `hybrid` 仍把 Hashing dense
限制在 sparse 候选内，避免本次评测功能暗改线上默认行为。

## 2. 评测协议

- 先取每路 `candidate_k=50`，再用 `top_k=10` 计算 Retrieval 与 Ranking。
- 只用 20 条 calibration case 选择每路 Gate 阈值；20 条冻结 test 不参与选阈值。
- Gate 优先满足 answerable recall 不低于 `0.90`，再优化 abstain F1、answerable F1 和 accuracy。
- Context 使用与产品相同的 token budget 打包器，默认预算为 3000 tokens。
- Generation 暂时使用 deterministic extractive proxy：回答只能由已检索 excerpt 拼接，报告
  required claim token recall 和 forbidden claim exact match。由于当前标准 claim 主要为中文、官方
  快照主要为英文，token recall 只作为跨语言抽取 completeness 下界，不设通过阈值。它不是 LLM
  judge，也不代表生成模型回答质量。
- Citation 对每条上下文证据重新解析 citation，并核对 chunk 与 corpus content hash。
- System 记录查询 P50/P95、错误数、上下文 token 和本地 Hashing 的估算费用 0。

## 3. 失败分类

每条 case 只记录一个 primary failure，顺序用于避免同一失败被重复计数：

1. `system`：检索执行异常。
2. `retrieval`：标准 passage 未进入 candidate pool。
3. `ranking`：进入 candidate pool，但未进入 Top-K。
4. `false_rejection`：标准证据在 Top-K，但 Gate 拒答。
5. `context`：Gate 接受且标准证据在 Top-K，但被 token budget 丢弃。
6. `citation`：上下文 citation 无法稳定解析或 revision 不匹配。
7. `grounding`：回答包含 forbidden claim 或未来 Generator 输出无法回到证据；本轮抽取式回答逐字
   来自 citation，低跨语言 claim token recall 不误判为 grounding failure。
8. `false_acceptance`：无答案 case 被 Gate 接受。
9. `none`：没有命中上述失败。

`ingestion` 在运行级统计；任一 approved source 摄取失败会使整次评测 fail closed，不生成可比较
报告，因此不会把缺语料后的 80 条 case 当作有效结果继续计分。

## 4. 复现合同

报告固定 dataset/cases/corpus hash、Provider revision、SQLite index corpus revision、source commit
和 dirty 状态。deterministic digest 不包含机器延迟、临时 page revision UUID 或 citation ID，只覆盖
输入、阈值、指标、稳定 passage 排名与失败层。

FTS、dense 和 RRF 同分候选统一使用 `source path + source revision + ordinal + content hash`
排序，`chunk_id` 只作最终兜底，避免临时建库 UUID 改变同分结果。

## 5. 非目标

- 不在 PR-2 引入 PostgreSQL、pgvector、HNSW、Cross-Encoder 或查询改写。
- 不用 frozen test 调 Gate，也不把 2 条 calibration 无答案题包装成可靠线上拒答结论。
- 不把 extractive proxy 当作真实 generation quality；真实语义 Provider 与 Gate 重校准属于 PR-4。
