# Sage 学习意图路由与分阶段 Eval 实施计划

> 日期：2026-08-06
> 状态：执行中
> 基线：`codex/book-learning-rag-design@5673226`
> 前置设计：`docs/superpowers/specs/2026-08-05-sage-book-learning-rag-design.md`

## 1. 本阶段产品目标

让 Sage 在用户不说“搜索知识库”时也能识别学习问题，并输出一个服务端可审计的路由：

```text
用户问题
  -> 学习意图、知识范围、问题深度、学习阶段
  -> 单次 RAG / Agentic 候选 / 不检索
  -> Retrieval Gate 选择 Knowledge、Web 或 Memory
  -> 分阶段 Eval 定位失败层
```

本阶段不训练 SFT 小模型，不自动启动 Research Agent，不评估生成答案忠实度。它先固定小模型
未来必须遵守的 schema，并建立 Agentic RAG 接入前的生产路由与比较尺。

## 2. 垂直切片

### Slice A：学习意图合同与规则基线

- 输出 `intent_family/knowledge_scope/depth/learning_stage/recommended_mode`；
- 显式的“只用某书”“不要联网”“不要工具”优先于模型或规则判断；
- 普通解释、比较和学习计划问题可以软路由到 Knowledge；
- `routing_confidence` 只表示路由置信度，不表示答案正确率；
- Provider 失败时回退到确定性规则，不阻塞主对话。

### Slice B：生产 Retrieval Gate 集成

- 复用现有 `decide_retrieval_gate()`，不建立第二套路由运行时；
- 公开 timeline 只输出类别、范围、深度和计数，不输出 query、topic 或 book ID；
- 比较、研究和系统学习等复杂问题只标记 `agentic_candidate`，本阶段不自动委派；
- resume 继续读取冻结的 Gate receipt，不重新分类。

### Slice C：分阶段 Eval

- 提交 24 条 `product_seed` case，dev/calibration/test 各 8 条；
- Eval 直接调用生产 Gate，报告意图、范围、深度、Agentic、来源约束和 exact route 指标；
- 每个失败只归入首个主要层：constraint、intent、scope、depth、agentic 或 source；
- 报告明确 seed 数据不是盲测、不是正式模型准确率，也不用于简历。

## 3. 验收证据

- “解释久期为什么受利率影响”在 Knowledge 可用时选择 Knowledge；
- “比较两本书的风险定义”标记为 `agentic_candidate`，但不自动创建 child；
- “不要联网”永远不选择 Web；
- timeline 和 durable context 不泄露原始 query；
- seed Eval 能按 split 和失败层输出指标；
- 旧 Retrieval Gate、resume、Knowledge/Web 工具范围行为保持兼容。

## 4. 后续边界

下一阶段才把 `agentic_candidate` 接到 Leader 的 retrieval sufficiency gate：只有单次 RAG 证据
不足或问题确实需要跨来源时才委派 Research；最后再加入生成、faithfulness、answer relevance
和端到端成本/延迟 Eval。

