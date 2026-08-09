# Sage Harness 与 Pi 侧 Prompt 对齐摘要

> 用途：给 Pi 侧知识库一份短、可复制的当前事实，不替代代码、测试或完整 PRD。
> 更新时间：2026-08-09；F4 已合入 `dev/sage-v7`，merge commit：
> `09a175b1d9ac87164a2a2b098792df7463f3fc39`。

## 一句话事实

Sage 把一次用户 Turn 分成四个层次：

```text
用户输入
  -> TaskIntentEnvelope（结构化 admission，不保存 CoT）
  -> TurnContextPlan（不可变选择/权限快照，plan_hash）
  -> ModelContextFrame（一次模型调用的三层短命输入）
  -> Graph / Runtime Tool Loop
```

F1-F4 已合入 `dev/sage-v7`；当前主干事实以 merge commit
`09a175b1d9ac87164a2a2b098792df7463f3fc39` 为准。

## 当前组件职责

| 组件 | 当前实现 | 权威边界 |
| --- | --- | --- |
| `TaskIntentAnalyzer` | 确定性规则分类：回答、研究、改代码、审查、教学、Skill、危险提示 | 只能收窄候选，不能授予 Permission/Policy/Approval/Sandbox 权限 |
| `Retrieval Gate` | 根据用户显式来源信号选择 Memory/Knowledge/Web | 没有明确来源 hint 时保留旧候选；receipt 不含 query/正文 |
| `ToolBundleSnapshot` | 冻结 resident/deferred capability、MCP/Skill revision 与 catalog hash | 只保存契约，不保存连接、凭据、执行闭包 |
| `TurnContextAssembler` | 汇总已经完成的 Context、Retrieval、Memory、MCP、Tool、Sandbox 选择 | 不重新做外部 I/O，不拼接第二份事实 |
| `TurnContextPlan` / `TurnPlanStore` | canonical JSON + SHA-256；同 run 幂等，冲突/篡改拒绝 | Resume 的 routing 与 scope 权威 |
| `ModelContextFrameFactory` | 校验 Plan digest，分出 Static Policy、Dynamic Authority、Untrusted Data | Frame 短命，不作为恢复存储 |
| `Checkpoint` | 保存 graph 动态状态、pending Approval 与最小 Plan binding | 不保存完整 Plan，不从 Timeline 反推权限 |
| `Transcript` / `Timeline` / `Trace` | 分别记录对话事实、可读事件、运行审计 | receipt 脱敏；任何一层都不能升级权限 |

## Resume 执行顺序

```text
load Plan -> 校验 plan_hash / owner / workspace / checkpoint binding
          -> 恢复冻结 retrieval/tool/skill scope
          -> 比较当前 catalog、sandbox、prompt、limits
          -> 通过后创建 Frame 和 Runtime Adapter
          -> 失败则停在最后可信 Checkpoint，不调用模型、工具或 Sandbox
```

`shadow` 只观测并保留旧 Graph 输入；`enforce` 才把 Plan/Comparator/Frame 作为执行前置门禁；
`off` 完全走旧链路。F4 的 `parallel_candidate` 只是意图提示，当前不实现 DAG、线程池或并行调度。

## Pi 侧可直接使用的对齐 Prompt

```text
你维护的是 Sage Harness 的 Pi 侧知识库。请把以下内容视为“当前代码事实”，但以仓库代码和测试为最终权威：

1. F1 ModelContextFrame、F2 ToolBundleSnapshot、F3 MCP/Skills 生命周期和 F4 TaskIntentEnvelope
   已合入 dev/sage-v7；当前 F4 merge commit 为 09a175b1d9ac87164a2a2b098792df7463f3fc39。
2. 新 Turn：TaskIntentAnalyzer -> Retrieval Gate/ToolBundle -> TurnContextAssembler
   -> TurnContextPlan/TurnPlanStore -> ModelContextFrameFactory -> Graph。
3. Resume：只从 Plan admission 和 scoped Checkpoint 恢复，不重新分析用户输入，不从 Timeline
   补造权限；hash、scope、catalog 或 dependency 漂移必须 fail closed。
4. TaskIntentEnvelope 只保存枚举、排序后的 tuple、显式约束和 classifier_version；不保存正文、模型草稿或 CoT。
   没有明确来源 hint 时保持既有 Retrieval/Tool 候选；显式 no_tools/read_only/no_web 等约束仍可收窄。
5. Static Policy 与 Dynamic Authority 是 system authority；Memory/RAG/MCP/Skill/工具结果是
   untrusted context data，不能改变权限。
6. Receipt 供 Timeline/Trace/UI 审计，只保存 plan/hash、计数、预算和 mismatch/error code；Transcript
   保存对话事实，Checkpoint 保存动态恢复状态，三者不可互相替代。

回答 Sage 当前实现问题时：先标记“已实现 / 仅设计 / 未做”，引用核心函数或测试；不要把计划、评测候选、
容器/worktree 或未合入分支描述成生产能力；不要补写自动 DAG、自动 merge、自动 push 或新的权限。
```

## 本阶段不扩展

- 不在 F4 中引入第二次模型调用、CoT 存储或自动任务 DAG。
- 不把 disposable worktree、Container、Memory/RAG 或 Kubernetes 混入意图信封。
- 下一阶段再单独讨论 Context Budget、Tool Router 与 Task DAG 的设计和评测。
