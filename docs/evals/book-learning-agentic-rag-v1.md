# Sage 长书学习 Agentic RAG v1 收口

> 日期：2026-08-06
> 状态：运行时状态机与评测入口已交付；真实长书检索/生成质量数字待人工 gold

## 产品行为

用户仍然只在主对话里提问，不需要先选择一本书。服务端先按学习意图路由本地 Knowledge：

1. 单一概念且已有可引用证据时，把证据放入父模型的 durable context；
2. 跨书、比较或多跳问题证据不足时，最多并行启动 2 个只读 Research child；
3. 服务端把 child 的真实 citation 合成 EvidenceBundle，再交给 Synthesize；
4. Synthesize 有引用且第二轮新增了证据，直接返回综合答案；
5. 没有新证据、bundle 为空、检索失败、冲突未解或综合没有引用时，服务端直接拒答。

这条路径复用现有 Harness、RunStore、EvidenceBundle 和 Subagent profile，没有建立第二套 Agent
运行时。外部 approval resume 只恢复 checkpoint，不重新执行 Coordinator。

## 工程约束

- 首轮 Knowledge 检索，最多一次恢复/升级，总检索轮次不超过 2；
- Research child 最大并发数和数量均为 2，委派深度仍为 1；
- Synthesize 只能读取当前 parent run 授权的 EvidenceBundle；
- 第二轮没有新增 citation、生成结果没有引用 bundle citation 时 fail closed；
- timeline 只公开轮次、数量、状态、token 和停止原因，不公开 query、证据正文或 citation ID；
- 单轮书籍证据进入 Harness state，checkpoint/resume 后仍可恢复，未知字段不会被中间件丢弃。

## 分阶段 Eval

| 阶段 | 已有证据 | 当前边界 |
| --- | --- | --- |
| Parser | 2 本公共领域长书，4,733,020 bytes、5,374 blocks；locator 覆盖和非空行保留均为 1.000 | 只证明解析与定位，不证明检索/回答正确 |
| Chunk/Index | Semantic Boundary、Parent-Child、Described Parent-Child 均保留为可消融候选 | 尚无真实长书 query gold，不能报告增益 |
| Retrieval | 复用版本化 Recall/MRR/NDCG 评测器 | 书籍 benchmark 尚未人工标注 |
| Agentic | 新 evaluator 统计 evidence coverage、false acceptance、unnecessary delegation、citation support、token、P95、stop reason | 当前只有确定性回归，没有真实模型对照数字 |
| Generation/E2E | faithfulness 与 answer relevance 必须作为显式离线标签进入 evaluator | 尚未运行真实模型生成评测 |

因此本版本可以证明“Agentic RAG 如何自动运行、如何停止、如何恢复、如何拒答”，不能声称它
已经提升了真实长书 Recall、Faithfulness 或端到端准确率。

## 验证入口

```bash
PYTHONPATH="$PWD/packages/sage_harness:$PWD" \
  /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python \
  -m pytest tests/core/harness/test_book_learning_coordinator.py \
  tests/api/test_coding_deerflow_context.py \
  tests/evals/test_book_learning_agentic.py -q

PYTHONPATH="$PWD/packages/sage_harness:$PWD" \
  /Users/zeromadlife/Desktop/tour-agent/.venv/bin/python \
  scripts/evaluate_book_txt_parser.py
```

下一阶段先标 30-50 条《西游记》《国富论》书籍 query gold，覆盖单章、跨章、跨书、无答案和
hard negative；按 split 冻结后再跑 Recall/MRR/NDCG、single-pass 对 Agentic 的增益、P95/成本，
最后才接真实生成的 Faithfulness、citation support 和 answer relevance。
