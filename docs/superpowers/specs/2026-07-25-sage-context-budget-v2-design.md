# Sage Context Governance v2.1 设计

## 1. 难题不是“模型忘了”这么简单

长 Shell / Search 工具循环出现过一种典型症状：任务开始时已经确认的目标或证据，随着
上下文增长，在后续回答中不再稳定出现。最初很容易把它归因于模型能力或长期 Memory，
但代码和可复现测试最终定位到三种上下文失真叠加：

1. **压缩不可达**：旧六级压力状态按约 `936k` 有效硬窗口计算，`compact` 约在 `608k`
   才触发；单 run token 上限为 `250k`，正常任务会先耗尽 run budget。
2. **摘要双重投影**：同一 handoff 同时作为普通 `HumanMessage` 和 `summary_text` 注入；当
   `durable_context` 中还有旧摘要时，旧值又可能覆盖顶层新值。模型看到的不是一份历史，
   而是两份甚至新旧冲突的历史。
3. **工具证据过早清理**：仅凭“完整结果已落盘”就删除活跃预览，虽然字节仍可恢复，模型
   却未必知道何时需要回载；若后续模型尚未形成文本承接，关键决定会从当前工作集消失。

因此本版本不把问题包装成“新增一个总结器”，而是把上下文改为三层治理，并用失败测试
分别冻结以上三条根因。长期 Memory 的 consolidation / retraction 是另一条状态生命周期，
不与本次上下文问题混合归因。

## 2. 外部调研与 Sage 的取舍

### 2.1 Hermes Agent

[Hermes Context Compression](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/context-compression-and-caching.md)
提供 gateway safety net 与 in-loop compressor 两级触发，优先使用 provider 报告用量，先清理
旧工具结果，再做结构化摘要，并保护最近用户消息与工具调用组。

Sage 借鉴了“循环内治理、真实用量校准、cheap prune 在 semantic compact 之前”的顺序，
但没有照搬无条件清理：只有已有 `artifact_ref`、位于保护尾部之外、且存在后续非空
AI 文本承接的工具结果才有 pruning 资格。否则宁可进入语义压缩，也不把“磁盘中仍存在”
误当成“模型仍然记得”。

### 2.2 Claude Code

[Claude Code 工作机制](https://code.claude.com/docs/en/how-claude-code-works)明确区分会话历史与
持久规则：接近窗口时先清理旧工具输出，再按需总结；长期必须保留的规则放入 `CLAUDE.md`
等持久层；连续压缩后又立即被超大结果撑满时停止自动 thrash。

Sage 因此把 durable context 和 conversation summary 分开管理，保留节省不足 `10%` 的
anti-thrash，并将失败分成 transient / configuration / context_overflow / internal，而不是
所有异常都使用同一重试节奏。

### 2.3 DeerFlow 与 LangChain

[DeerFlow Summarization](https://github.com/bytedance/deer-flow/blob/main/backend/docs/summarization.md)
及其[中间件源码](https://github.com/bytedance/deer-flow/blob/main/backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py)
基于 LangChain `SummarizationMiddleware`，保护 AI / Tool 对，把摘要保存在独立
`summary_text` channel，并通过隐藏的低权威 durable data 临时投影，而不是把摘要伪装成
新的用户指令。

Sage 采用同一原则：`summary_text` 是摘要唯一真相源；普通消息只保留未压缩尾部；当前顶层
state channel 覆盖兼容性的 nested durable snapshot。摘要调用不向 UI 流式输出，失败时
保留原消息。

## 3. 三层治理模型

### 第一层：确定性、可恢复的工具结果治理

- 结果超过 `16 KiB` 时完整 offload 到 session/run scoped Artifact Store；活跃消息只保留
  最多 200 行、12,000 字符预览与 `artifact_ref`。
- 当工作集达到 `70%` 时，扫描保护尾部之外的旧 ToolMessage。
- 仅当完整 Artifact 已存在且后续 AI 已形成非空文本承接，才把预览替换成带
  `artifact_ref` 的恢复标记；未消费的最新结果绝不清理。
- `load_artifact` 按同 session、最大 `16 KiB`、UTF-8 byte cursor 分页回载完整证据。
- pruning 是独立 Timeline 事件，不伪装成语义摘要。

曾评估把 offload 阈值从 `16 KiB` 降至 `4 KiB`。固定任务上模型输入和 checkpoint 均无
改善，只增加小结果落盘，因此生产默认继续使用 `16 KiB`。这也是本次“用消融否决方案”
而非凭直觉调参的例子。

### 第二层：语义工作集压缩

- LangGraph 每次 `before_model` 计算 `messages + summary_text + static overhead`，因此同一
  ReAct 工具循环内也会触发。
- 默认 `working_set=32k`、`keep=12k`；retrieval-only Surface 使用 `16k/8k`。
- 最近真实用户消息和完整 AI / Tool 调用组固定保留。
- 摘要仅写入 `summary_text`，由 DurableContextMiddleware 以隐藏低权威数据投影一次。
- 最近 provider `input_tokens` 用于校准近似计数；校准基线已包含 system / summary 静态
  开销，避免再次重复计算摘要。
- 摘要失败 fail open；transient 首次失败冷却 30 秒，配置错误冷却 300 秒，未知错误连续
  两次后冷却；节省不足 `10%` 连续两次也停止自动重试。

### 第三层：硬窗口与运行安全

旧 `normal / budget / snip / compact / high / emergency` 六档继续存在，但它们是**一层中的
六个压力状态**，不是六种连续有损裁剪。它们负责：

- 按模型 context window 与 output reserve 投影硬窗口压力；
- 在 high / emergency 状态限制注入并阻止不安全模型请求；
- 与 run token、model call、tool call 和 wall-time budget 共同形成最终保险；
- 保持原 Timeline、checkpoint 与恢复契约兼容。

所以“六级是否过于激进”的答案是：如果六档都执行独立裁剪会过于激进；当前实现没有这样
做。日常治理由前两层完成，六档只描述第三层的压力和最终动作。

## 4. 一次模型调用前的流转

```text
Tool result
  -> Artifact offload (only large results)
  -> working-set measure (estimate + provider calibration)
  -> recoverable prune (only consumed artifact-backed history)
  -> semantic compact (only if still over 32k)
  -> hard-window / run-budget gate
  -> model request
```

关键不变量：最新用户目标不被摘要覆盖；未消费工具证据不被 cheap prune；摘要只有一个
state source；任何摘要失败都不删除原消息；完整工具结果仍受 session scope 校验。

## 5. 消融协议

固定 13 条任务包含长 Shell/Search 结果、Unicode、Artifact 中部探针，以及无法 Artifact
offload 的长检索链。变体按层累加：

| 变体 | 16 KiB offload | 4 KiB 候选 | recoverable prune | semantic compact | reload |
| --- | --- | --- | --- | --- | --- |
| A0 previous offload | 是 | 否 | 否 | 否 | 否 |
| A1 offload 4k | 否 | 是 | 否 | 否 | 否 |
| A2 recoverable prune | 是 | 否 | 是 | 否 | 否 |
| A3 semantic compact | 是 | 否 | 是 | 是 | 否 |
| A4 full | 是 | 否 | 是 | 是 | 是 |

安全门禁包括：最新用户原文、存活工具调用对、决策标记和 Artifact 探针。只有四项均通过的
阈值才能按净 token 节省评分。确定性摘要用于隔离机制随机性，不冒充自然语言摘要质量。

## 6. 当前证据与边界

精确数字见 `evals/reports/context_budget_v2_1_2026-07-25.json` 与
`docs/evals/context-budget-v2.md`。当前仍未证明：

- 真实摘要模型的 required-fact 保留率和任务完成率；
- Provider 实际账单 usage、P50/P95 延迟和摘要调用成本；
- Provider context-overflow 后在同一模型调用内自动压缩并重试；
- 长期 Memory 自动抽取、冲突合并、TTL 与语义 consolidation。

下一阶段应冻结 Provider / model / temperature，用相同任务先跑改造前 baseline，再跑三层治理，
记录 required facts、citation correctness、任务成功率、实际 usage 和延迟。只有真实模型结果
通过，简历才可以从“确定性机制评测”升级为“端到端任务质量提升”。

## 7. 可用于面试的真实问题解决链

这段经历应表述为：长工具任务出现目标和证据不稳定，最初怀疑长期记忆；通过阈值计算发现
压缩根本不可达，再用失败测试复现摘要重复投影；参考 Hermes / Claude Code / DeerFlow 后
提出三层治理。第一版照搬 cheap prune 又导致决策标记消失，因此增加“后续模型文本承接”
资格，并用消融否决 4 KiB offload。最终保留数据支持的机制，未通过真实模型评测的部分继续
明确为边界。不要虚构线上事故，也不要把确定性 marker 测试描述为回答准确率。
