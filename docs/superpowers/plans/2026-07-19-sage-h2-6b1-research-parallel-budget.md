# Sage H2.6B1 Research 受限并行与共享预算实施契约

> 日期：2026-07-19
>
> 状态：已完成发布门禁，待 PR 合入 `dev/sage-v7`
>
> 基线：`dev/sage-v7@2bb0aa9`

## 1. 目标

在 H2.6A 单个只读 Research 子代理上，开放最多三个子任务的真实并行执行，并让父 run 与所有 child 共用同一个 token、model call 和 tool call 安全预算。并行完成顺序不得改变 evidence refs，checkpoint 恢复不得重复登记同一 child。

本切片只建立安全的并行与账本底座。Evidence Bundle 读取端口、重复来源 breaker 和只读 Synthesize profile 尚未具备，因此不在本切片伪造综合能力。

## 2. 运行契约

- LangGraph 原生 ToolNode 执行同一 AIMessage 中的多个 `task` 调用，服务端最多保留三个。
- `child_run_id` 由 `thread_id + parent_run_id + tool_call_id` 派生；恢复时按派生身份去重。
- 每个新 child 在执行前写入 durable delegation ledger，记录服务端 profile、预留预算和终态实耗。
- 运行中 child 按预留 token/model/tool 额度计入父预算，避免并发批次过量预订。
- child 终态后按实际 token/model/tool 计数结算并释放未使用额度。
- 超时但无法取得完整实耗时按预留额度 fail-closed 结算；不能靠重复超时绕过预算。
- Provider 完全不返回 usage metadata 时，只要 child 已请求模型，也按预留 token 额度 fail-closed 结算。
- 父 timeline 的 `run_budget_updated` 展示父模型与全部当前 child 的合计值。

## 3. 证据与恢复契约

- child 只返回 result brief、result ref 和服务端 evidence refs，不注入完整 transcript。
- 全局 `evidence_refs` 使用有界、去重、稳定排序 reducer；三个 child 的完成顺序不影响结果。
- terminal delegation 保留 `token_usage`、`model_calls`、`tool_count` 和 `evidence_refs`。
- 同一 checkpoint 重放同一 tool call 时复用派生 child 身份，不增加总委派数或重新预留预算。
- 其他 run 的历史 delegation 不进入当前 run 的预算汇总。

## 4. 安全边界

- 仍只允许服务端注册的 `explore` 与 `research` profile。
- 不开放 Shell、写文件、Memory/Knowledge 写入、递归子代理或模型自定义预算。
- 并发默认上限为 3，总 child 上限仍为 6，嵌套深度仍为 1。
- 子 `max_steps` 会被父剩余 model/tool 额度下调，不能仅靠 child 自身上限绕过父 run 上限。
- 本切片不修改前端 wire schema；现有 timeline 与 Chat Dock 自动消费真实事件投影。

## 5. 本切片不交付

- Evidence Bundle Store/Port 及按 citation ref 读取已验证证据正文。
- `synthesize` profile 与冲突/缺口综合。
- 重复查询、重复来源和无新证据 breaker。
- Node Research Task/Research Branch 持久化实体。
- `primary_goal_id`、frozen surface-context receipt 和 RAG chunk/trimming receipt。
- 自动写入 Wiki、Knowledge Unit、Memory、Mastery 或 Plan。

## 6. 验收

1. 同一父模型消息包含三个 `task` 时，三个 child 确实同时进入执行，第四个被服务端拒绝。
2. 三个 child 的 token/model/tool 预留总和不超过父 run 剩余额度。
3. child 终态后父预算使用实际计数，运行中与超时路径均不能绕过上限。
4. checkpoint 重放同一 tool call 不重复登记 child。
5. 并发完成顺序不改变去重后的 evidence refs。
6. timeline 展示父子合计预算，不泄露 child prompt、tool args 或远程正文。
7. 后端定向与全量测试、Ruff、mypy、前端回归、生产构建和 `git diff --check` 通过后，才允许 PR 合入 `dev/sage-v7`。

## 7. 下一切片

H2.6B2 先提供只读 Evidence Bundle 端口，再实现重复查询/来源 breaker 与 `synthesize` profile。Synthesize 只能读取当前父 run 已落账的 evidence refs，不得重新访问网络，也不得直接沉淀长期知识。

## 8. 发布门禁记录

- 定向 Harness 回归：`100 passed`。
- 后端全量回归：`1631 passed`（仅保留已有 GPT-2 tokenizer warning）。
- 相关 mypy：263 个源码文件通过；本切片复核的 9 个源码文件通过。
- Ruff：全仓 `ruff check` 通过。
- 前端回归：59 个文件、436 个测试通过；生产构建通过（仅已有 chunk size warning）。
- `git diff --check` 通过。
- 全仓 `ruff format --check` 仍受基线中 153 个未触及文件影响，本切片未扩散格式化修改；CI 质量门禁使用 `ruff check`。
