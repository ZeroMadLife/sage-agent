# Sage H2.6A 受限 Research 子代理实施契约

> 日期：2026-07-19
>
> 状态：已通过 PR #40 合入 `dev/sage-v7`
>
> 基线：`dev/sage-v7@b001441`

## 1. 目标

在不复制 Chat Runtime、不扩大模型权限的前提下，为现有 `task` 委派能力增加一个服务端固定的 `research` profile，使父 run 能委派一次 Knowledge + Web 只读研究，并通过 durable timeline 观察安全的进度与证据数量。

本切片解决“单个研究子任务如何受限执行和回到父循环”，不解决并行编排、长期目标进度或自动沉淀。

## 2. 固定安全边界

- Profile、工具范围、系统提示和预算由服务端注册，模型只能选择已注册 profile 并描述任务。
- `research` 允许 `list_files`、`read_file`、`search`、`knowledge_search`、`search_web`；`fetch_web` 仅在服务端真实配置时加入。
- 禁止 Shell、文件写入、Memory/Persistence、Knowledge 写入、审批代理和子代理嵌套。
- 子 run 独立限制 `24000 tokens / 16 steps / 180 seconds`，不能由请求参数提高。
- Web 内容始终按不可信远程证据处理，不能把网页指令升级为 Harness 指令。
- 子 transcript 不注入父上下文；父循环只获得 result brief、服务端 evidence refs 和 child result ref。

## 3. 运行与审计契约

父 timeline 可记录：

```text
subagent_started
subagent_progress {phase, status, tool, tool_count, evidence_count, operation_ref}
subagent_completed | subagent_failed | subagent_cancelled
```

`subagent_progress` 必须过滤 tool arguments、网页正文、prompt、模型私有内容和异常内部信息。子 run 保留完整的 durable trace，并在终态记录 result、evidence refs 和 evidence count。

Capability Registry 只有在当前 runtime 同时具备真实 `KnowledgeStore` 与 Web Search 时才暴露 `subagent:research`。Fetch 缺失只缩小工具范围，不让整个 profile 伪装不可用。

## 4. 兼容性

- `explore` 继续复用原有 `task` 工具和遥测标识，行为保持兼容。
- 动态工具参数也走统一 validator；Pydantic 校验错误转换为现有 `ToolArgumentValidationError`，允许模型按既有循环修正参数。
- 空工具映射表示显式无权限，不能回退到全局工具注册表。
- 主对话和 Knowledge 继续共用现有 Coding store/runtime/timeline；本切片不修改前端 wire schema。

## 5. 本切片不交付

- 多个 Research child 并行与 Synthesize。
- Practice profile 和 Mastery Evidence。
- Node Research Task/Research Branch 的持久化实体。
- `parent_thread`、`primary_goal_id`、`run_id` 的正式研究任务绑定。
- graph/page/source revision 的 frozen surface-context receipt。
- RAG page/source/chunk/revision/trimming/token-budget receipt。
- 自动保存 Wiki、Knowledge Unit、Memory、Mastery 或 Plan。

## 6. 验收

1. 在 Knowledge 与 Web Search 可用时，Capability Registry 返回可提升的 `subagent:research`。
2. 父模型通过 `task(subagent_type=research)` 启动 child，child 只能调用固定只读工具。
3. 子代理获得 Knowledge 与 Web evidence 后返回带引用的摘要，父模型继续生成最终回答。
4. 父 timeline 可看到启动、工具摘要、证据数和终态，但看不到参数与正文。
5. 缺少 Knowledge 或 Web Search 时 fail closed，不暴露 Research capability。
6. 非法动态工具参数进入现有可重试校验错误，而不是导致 run 崩溃。
7. 本切片匹配测试、后端静态检查、前端回归、生产构建和 `git diff --check` 通过后，才允许 PR 合入 `dev/sage-v7`。

## 7. 发布门禁证据

- 后端定向门禁：`285 passed`。
- 后端全量：`1618 passed`（已包含 PR #37 Knowledge 节点研究前端基线）。
- 前端全量：`59 files / 436 passed`。
- Ruff：`All checks passed`。
- mypy：`263 source files` 无问题。
- 前端生产构建：通过；仅保留既有 chunk size 提示。
- `git diff --check`：通过。

门禁期间发现并关闭一个兼容性回归：既有 profile parity 测试通过 monkeypatch `api.coding.SubagentToolConfig` 缩短 Explore timeout。实现保留该服务端配置注入点，并只在其上追加固定 Research profile，因此旧 Explore 测试与 Research 安全边界均保持成立。
