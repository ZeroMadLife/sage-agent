# Sage H2.6C Practice Profile 实施计划

> 日期：2026-07-19
> 状态：实现完成，等待发布门禁
> 基线：`dev/sage-v7@c733465`

## 1. 目标

在唯一 Chat Harness 中注册服务端静态 `practice` 子代理，使主对话可以把一次代码练习、测试或小实验委派为独立 child run。Practice 必须复用当前会话的 Tool Policy、Approval、Sandbox、workspace 和运行预算，并只返回可审计结果摘要与结构化 `MasteryEvidenceCandidate`。

## 2. 本切片交付

1. `practice` profile 只允许 `list_files`、`read_file`、`search`、`write_file`、`patch_file` 和 `run_shell`。
2. child 执行通过父会话的 `ToolExecutor`，危险命令、写入、路径 containment 和 Sandbox 行为与主运行一致。
3. child 审批事件回到父 timeline，用户仍通过现有审批接口决定，不新增第二套审批协议。
4. 只有可识别测试命令的真实 `exit_code` 生成 `code_test` 候选；退出码 `0` 为 `pass`，非零为 `fail`。
5. 候选使用确定性 ID 和 child run source ref，进入 child trace 与父 delegation receipt；不写入 Mastery Ledger、Memory、Wiki 或 Knowledge。
6. 失败、取消、超时、无测试工具结果和仅有模型文本时均不能生成通过证据。

## 3. 非目标

- 不实现 Goal Contract、Mastery Ledger 或掌握度百分比；这些属于 H2.7。
- 不实现开放题 LLM Judge 或自动测验评分。
- 不允许 Practice 访问 Web、MCP、Knowledge 写入、Memory 或递归 `task`。
- 不修改主对话和 Knowledge 前端布局。

## 4. 验收证据

- profile/Capability Registry 能发现 `subagent:practice`；
- 通过和失败测试命令分别产生 `pass`/`fail` 候选；
- 普通成功 Shell 不产生 Mastery Evidence；
- 写入和 Shell 仍受父权限、审批与 Sandbox 约束；
- child 审批事件可以由父 timeline 投影；
- Research、Synthesize、Explore 回归保持通过。

## 5. 实现记录

- `practice` 已作为服务端静态 profile 加入 Capability Registry，能力 ID 为
  `subagent:practice`；模型不能修改工具范围、预算或权限。
- Practice 使用父会话的 `PermissionChecker`、`ToolPolicyChecker`、
  `ApprovalManager` 和 `SandboxPort`。子任务审批在当前 child 原地等待，事件带
  `approval_scope=subagent`，不会错误触发父 LangGraph checkpoint 二次恢复。
- 同一父 Run 最多并发一个 Practice，避免多个写入型 child 竞争同一工作区；
  Research 仍保留受限并行能力。
- 仅最终、未被 `|| true`、管道或分号掩盖的确定性测试命令退出码可生成
  `MasteryEvidenceCandidate`。候选进入 child trace 与父 delegation receipt，
  不进入掌握度事实源。
- 审批参数进入 timeline 前会做长度限制和常见 token/password/secret 赋值脱敏。

## 6. 仍未交付

- `LearningGoal`、`capability_id`、rubric 绑定和 Mastery Ledger 属于 H2.7C；
- 开放题评分、测验题库和产物 rubric 尚未实现；
- 本切片没有新增前端专用状态，继续由现有 timeline/subagent receipt 投影。
