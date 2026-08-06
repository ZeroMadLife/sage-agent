# Sage 学习意图路由与分阶段 Eval v1

> 日期：2026-08-06
> clean source：`849241d44f49563b1e53066f110a808e47059332`
> 数据集：`book_learning_intent_v1_seed.jsonl`，24 条 `product_seed`
> 报告：[book_learning_intent_product_seed_v1_2026-08-06.json](../../evals/reports/book_learning_intent_product_seed_v1_2026-08-06.json)
> 报告 SHA-256：`6004b81b65b98de87a885acb4ec88b4c44dce9b894740336c4472e1c17a248af`

## 产品结论

Sage 现在不要求用户先说“搜索知识库”。普通学习问题会先经过结构化意图路由，再由同一套
Retrieval Gate 选择 Knowledge、Web 或跳过检索。例如“久期为什么会随着利率变化”会自动选择
Knowledge；跨书比较会标记为 `agentic_candidate`，但本阶段不会自动启动子 Agent。

路由同时识别问题类型、知识范围、直接/多跳深度和学习阶段。用户明确说“不要联网”或“不要
调用工具”时，服务端硬约束拥有最终决定权，小模型未来只能提出建议，不能扩大来源或工具权限。

本阶段交付的是 Agentic RAG 前面的可审计决策层和分阶段诊断尺，不是完整 Agentic RAG，也没有
评估最终答案的忠实度。

## 第一轮 seed 结果

| 指标 | 结果 |
| --- | ---: |
| 完整路由准确率 | 0.9167（22/24） |
| 意图准确率 / Macro F1 | 0.9583 / 0.9683 |
| 范围准确率 / Macro F1 | 1.0000 / 1.0000 |
| 深度准确率 | 0.9167 |
| 学习阶段准确率 | 1.0000 |
| Agentic mode 准确率 / F1 | 0.9167 / 0.9091 |
| 来源选择准确率 | 1.0000 |
| 用户约束遵循率 | 1.0000 |

这些数字来自人工设计的 product seed，用来验证 schema、生产接线和失败归因。它们不是盲测，
不是正式小模型准确率，不代表真实书籍问答效果，也不能作为当前简历指标。

## 暴露的问题

1. `intent-cal-001`：“结合知识库和官网最新资料解释 ETF 申赎机制”被规则判为 `research`，而
   seed 期望 `explain`。这说明“最新”信号优先级可能盖过用户的主要学习动作，并连带把任务升级为
  多跳 Agentic 候选。
2. `intent-test-001`：“久期为何会受到利率方向的影响”因为出现“影响”被判为 `multi_hop`，而
   seed 期望直接解释。这说明单个关系词不足以证明需要多跳，当前规则存在不必要升级风险。

本轮没有根据 test 失败反调规则。下一版应只用新增 dev/calibration 变体设计更精确的多跳判据，
然后重新冻结独立 test；不能把看过的测试题调成 100% 后仍声称 `test_used_for_tuning=false`。

## 工程合同

- `LearningIntentRoute` 固定未来规则/SFT 小模型共同遵守的结构化 schema；Router 不可用时回退
  确定性规则，不阻塞主对话。
- Retrieval Gate receipt 升级为 v2；公开 timeline 只暴露类别、置信度与候选数量，不包含 query、
  topic、book ID 或 chapter ID；私有 durable context 才保存有界候选。
- Eval 直接调用生产 `decide_retrieval_gate()`，避免离线评测一套路由、线上运行另一套路由。
- 每个失败按 constraint、intent、scope、depth、learning stage、agentic、source 的顺序归入一个
  主要层，同时保留各层检查结果，便于判断该改哪一层。

## 验证证据

- 后端完整门禁：`1756 passed, 11 skipped`；跳过项为需要 PostgreSQL live 环境的条件测试。
- Ruff：411 个文件格式和 lint 通过；mypy：212 个源文件通过。
- 前端完整门禁：69 个文件、505 项测试通过。
- 前端主应用与 public 两套生产构建通过；主应用保留既有大 chunk warning。
- `git diff --check` 通过。

## 下一阶段

1. 接入 retrieval sufficiency gate：只有单次 RAG 的证据覆盖不足、来源冲突或确需跨来源时，Leader
   才委派 Research；记录升级原因、检索轮次、child 数、预算和停止原因。
2. 建立真实长书 benchmark：优先补 TXT/EPUB 定位信息和不规则章节解析，再分别测 parser、chunk、
   retrieval，不能直接用最终答案掩盖前级失败。
3. 在真实长 block 上比较固定分块、语义断点和 described Parent-Child，关注 Recall/MRR/NDCG、
   citation 定位、索引成本与检索延迟。
4. 最后加入生成阶段与端到端 Eval：faithfulness、answer relevance、citation precision/coverage、
   拒答、成本和 P95 延迟，形成“意图 -> 检索 -> Agent -> 生成 -> 端到端”的完整回路。
