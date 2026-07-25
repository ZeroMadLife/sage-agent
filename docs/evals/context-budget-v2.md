# Context Budget v2 消融评测

## 1. 为什么改

旧六级 `ContextPolicy` 仍是必要的模型硬窗口安全层，但默认有效窗口为 936,000 token，
`compact` 阈值约为 608,400 token；单次 run 上限只有 250,000 token，因此日常长工具任务在
到达 run cap 前不可能触发压缩。超过 16 KiB 的工具结果虽然会 offload，模型也只有预览和
`artifact_ref`，却没有可调用的受控回载闭环。

Context Budget v2 把两个职责拆开：

- 六级状态继续负责硬窗口投影、emergency 阻断与旧恢复契约；
- LangGraph 在每次 `before_model` 使用独立图工作集预算，同一 ReAct 工具循环内也可压缩；
- `load_artifact` 只允许读取同 session Artifact，单页最多 16 KiB，使用无损 UTF-8 byte cursor；
- 摘要失败或节省不足时不替换原消息，连续无效尝试进入 checkpoint-safe cooldown。

## 2. 固定评测协议

- 数据集：`evals/context_budget_v2_cases.json`；
- Case：12 条确定性长工具任务，覆盖多轮工具调用、不同结果体积和 Unicode 内容；
- 实现：生产 cutoff、工具调用对保护、状态替换、Artifact Store 和分页读取；
- 摘要：marker-preserving 确定性替身，用于隔离模型随机性；
- 指标：峰值/累计模型输入估算、checkpoint 消息内容字节、摘要 token 成本、当前用户意图、
  工具调用对、决策标记与 Artifact 中部探针；
- Source commit：`6ec7d6fca8a7c3d52d09cf31791f1e171e9f03db`，生成报告时工作区 clean。

```bash
PYTHONPATH="$PWD:$PWD/packages/sage_harness" \
  python -m evals.context_budget
```

机器可读证据：`evals/reports/context_budget_v2_2026-07-25.json`。

## 3. 消融结果

| Variant | 计量 | 循环内压缩 | Artifact 回载 | 累计输入 | 相对 A0 | Checkpoint 内容 | Artifact 探针 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| A0 baseline | 否 | 否 | 否 | 2,177,535 | 0 | 1,321,598 B | 0% |
| A1 budget | 是 | 否 | 否 | 2,177,535 | 0 | 1,321,598 B | 0% |
| A2 compact | 是 | 是 | 否 | 1,861,774 | -14.50% | 905,316 B | 0% |
| A3 full | 是 | 是 | 是 | 1,861,774 | -14.50% | 905,316 B | 100% |

优化后 A3 的峰值模型输入估算从 50,530 降到 29,433，checkpoint 消息内容下降 31.50%。
7 次压缩额外消耗估算 107,461 token，占 A0 累计输入的 4.94%；扣除该成本后净 token 下降
9.57%。12 条 case 的最新用户意图、存活工具调用对和决策标记保留率均为 100%。

A1 与 A0 相同，证明“只计量”不会改善增长；A2 与 A3 的输入相同，证明 Artifact 回载解决
的是完整证据可恢复性，而不是通过偷偷增加常驻上下文获得节省。

## 4. 阈值扫描

| Working set / keep | 压缩次数 | 累计输入变化 | Checkpoint 变化 | 扣除摘要成本后的净变化 |
| --- | ---: | ---: | ---: | ---: |
| 32k / 12k | 7 | -14.50% | -31.50% | -9.57% |
| 48k / 18k | 1 | -1.24% | -8.10% | +0.02% |
| 64k / 24k | 0 | 0 | 0 | 0 |
| 80k / 30k | 0 | 0 | 0 | 0 |

预优化 `64k/24k` 在这批真实规模的合成任务上零触发，说明阈值仍然过松。四项安全门禁全部
通过后，`32k/12k` 的预声明 selection score 最高，因此只调整一次并固化为默认值；公开
retrieval-only Surface 进一步限制为 `16k/8k`。

## 5. 证据边界

本报告证明的是机制成本和安全不变量，不证明自然语言摘要质量。token 为 provider-neutral
近似值，不是账单 usage；耗时不包含真实摘要模型网络调用，不能作为线上延迟指标。

下一步应使用冻结模型、Provider、温度和任务集，评估压缩前后的 required facts、引用正确性、
任务完成率、P50/P95 延迟与实际 usage。长期 Memory 的抽取、冲突、consolidation 和撤回属于
单独评测切片，不能与本报告混合归因。
