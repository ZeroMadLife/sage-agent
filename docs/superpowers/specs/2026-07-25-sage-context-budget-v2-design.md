# Sage Context Budget v2 设计

## 1. 为什么必须改

当前 Sage 已经具备上下文计数、六级压力状态、轮次边界压缩和大工具结果 Artifact 化，
但它们尚未组成可验证的闭环：

1. 六级压力状态按模型硬窗口计算。当前默认模型有效输入上限约为 `936k` token，
   `compact` 要到约 `608k` token 才触发；而单次 Harness 累计预算只有 `250k` token，
   因此正常运行通常先耗尽 run budget，压缩根本不可达。
2. 自动压缩只发生在新用户轮次开始前。Shell、搜索和子任务结果在同一轮 ReAct 循环中
   持续增长时，没有模型调用前的工作集治理。
3. 大工具结果超过 `16 KiB` 后会 offload，但模型只有截断预览和 `artifact_ref`，
   没有通过作用域校验的按需分段读取工具。“on-demand load” 目前只能算设计，不能算交付。
4. 现有测试主要验证局部不变量，尚未把 baseline、单项能力和组合能力放在同一批长任务上做消融。

这会造成两个实际风险：长工具循环的 prompt 和 checkpoint 继续膨胀；简历虽然描述了正确方向，
但无法给出“何时触发、降低多少、是否保留当前任务、是否能找回完整工具证据”的机器可读证据。

## 2. 设计目标与非目标

本版本交付四件事：

- 将原六级机制明确为模型硬窗口安全层，不承担日常工作集控制；
- 在 LangGraph 每次模型调用前执行可达的工作集计量和循环内压缩；
- 为同一 session 的工具结果提供有界、只读、可审计的 Artifact 分段回载；
- 建立固定语料的消融评测，先记录 baseline，再比较各层能力并做一轮阈值优化。

本版本不改长期记忆写入、冲突、合并和撤回语义。Memory v2 要用单独的评测集和 PR，
否则无法区分上下文压缩与长期记忆对结果的贡献。

## 3. 双层预算模型

### 3.1 硬窗口安全层

现有 `ContextPolicy` 和六级状态继续使用模型声明的 context window 与 output reserve。
它负责轮次入口投影、极端情况下阻止模型请求，以及兼容原有 Timeline 和恢复路径。
它不是日常压缩阈值，事件中必须标明 `budget_scope=hard_window`。

### 3.2 Graph 工作集层

新增 Harness 配置：

- `context_compaction_enabled`：是否启用循环内压缩；
- `context_working_set_tokens`：单次模型输入的操作上限；
- `context_keep_tokens`：压缩后保留的最近消息目标；
- `context_summary_input_tokens`：生成摘要时允许读取的历史上限；
- `context_static_overhead_tokens`：对 system prompt、工具 schema 和动态 durable context 的保守预算。

工作集从 run budget 推导，而不是模型宣传窗口。消融前候选值为 `64k / 24k`；固定数据集扫描后，
普通 Coding Surface 最终采用 `32k / 12k`，公开检索 Surface 采用更保守的 `16k / 8k`。
阈值扫描和优化前后结果必须同时保留在机器可读报告中，避免只展示有利结果。

每次模型调用前都发出 `context_usage_updated`，至少包含 estimated input、工作集上限、
工作集比率、累计 run token 和剩余 run token。Provider 返回 usage 后，下次计量保留
provider 报告的最近输入作为校验信息，但不同模型的 usage 不能混用为另一模型的精确计数。

## 4. 循环内压缩

压缩中间件位于 `durable_context` 之后、`run_budget` 之前。它在工具结果回到图后、
下一次模型调用前同样执行，因此不依赖用户开始新一轮。

压缩流程：

1. 对 state messages 做近似 token 计数并加上静态开销；
2. 未达到工作集阈值时只发计量事件；
3. 选择安全 cutoff，禁止拆开 `AIMessage.tool_calls` 与对应 `ToolMessage`；
4. 将旧消息与上一版摘要合成为结构化 handoff；
5. 保留最近消息，使用 `RemoveMessage(REMOVE_ALL_MESSAGES)` 原子替换 graph state；
6. 摘要调用的 token 计入当前 run budget；
7. 压缩前后节省不足 `10%` 视为无效，原消息保持不变；连续两次无效后进入持久化 cooldown；
8. 摘要模型失败、返回空文本或替换后仍不满足安全条件时 fail open：不修改消息，发失败事件。

结构化摘要只保留：最新用户目标、约束、已完成动作、关键决策、文件/Artifact 引用、
未完成步骤和已知失败。它属于有界的历史 handoff，不得覆盖最新用户消息或 server-owned durable context。

## 5. Artifact 分段回载

新增 `load_artifact` resident tool，参数为 `artifact_ref`、`offset_bytes` 和 `max_bytes`。

- 单次最多读取 `16 KiB`，返回 `content`、`offset_bytes`、`next_offset_bytes`、
  `total_bytes` 和 `truncated`；
- byte cursor 必须位于 UTF-8 字符边界；页尾遇到不完整字符时回退并由下一页重读，
  不用 replacement character 损坏原始证据；
- 只接受 `sage://coding/{session}/runs/{run}/tool-results/{call}.txt`；
- session 必须与当前 thread 相同；run 可以是同一 session 的当前或历史 run；
- 文件打开继续使用 `O_NOFOLLOW`、普通文件和单硬链接检查；
- 非法 scope、越界参数、软链接和不存在的文件都显式失败；
- 工具只读，不授予工作区文件或其他 Artifact namespace 的访问能力。

## 6. 消融评测

固定长任务包含：多轮文本、包含工具调用对的长工具结果、当前用户意图、关键决策和
位于 Artifact 中部的探针。使用同一数据集比较：

| 变体 | 工作集计量 | 循环内压缩 | Artifact offload/load |
| --- | --- | --- | --- |
| A0 baseline | 否 | 否 | 仅 offload |
| A1 budget | 是 | 否 | 仅 offload |
| A2 compact | 是 | 是 | 仅 offload |
| A3 full | 是 | 是 | offload + load |

报告记录：峰值模型输入、累计模型输入、checkpoint 消息体积、压缩次数、摘要额外 token、
有效节省率、当前意图保留率、工具调用对完整率、Artifact 探针恢复率、失败保留率和运行时延。
确定性机制评测不冒充回答质量；后续 LLM judge 评测必须另报模型、provider、温度、延迟和逐条结果。

## 7. 验收标准

- 合成长工具循环无需新用户轮次即可触发压缩；
- 压缩后当前用户消息和完整工具调用对均保留；
- 摘要失败与无效压缩不会删除原消息；
- `load_artifact` 能恢复同 session 有界片段，跨 session 和非法路径全部拒绝；
- A0-A3 机器可读报告可复现，并同时给出绝对值与相对变化；
- 阈值只根据基线扫描优化一次，文档保留优化前后证据；
- 现有恢复、审批、工具治理和公开/private API 隔离测试不回归。

## 8. 评测后决策

`64k / 24k` 在固定 12 条长工具任务上没有触发压缩，说明它仍然无法治理当前工作负载；
`32k / 12k` 在预先声明的安全不变量全部通过时取得最高净 token 节省分数，因此成为本版本默认值。
这里的 token 是 provider-neutral 近似计数，摘要也是确定性替身；精确数字和限制以干净 source commit
生成的 `evals/reports/context_budget_v2_2026-07-25.json` 为准。
