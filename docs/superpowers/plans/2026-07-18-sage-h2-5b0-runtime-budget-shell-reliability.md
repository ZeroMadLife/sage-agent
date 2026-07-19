# Sage H2.5B0 本轮预算与 Shell 可靠性计划

**目标：** 在进入安全 Web Fetch 前，先关闭 H2.5A 联调暴露的两个运行时缺口：用户无法看到本轮累计 token；`run_shell` 超时后错误不可分类且可能遗留子进程。

## 1. 预算口径

Sage 同时保留两个不同指标，不再用一个“上下文”数字代替全部资源消耗：

| 指标 | 含义 | 当前上限 |
| --- | --- | --- |
| 上下文窗口 | 单次模型请求中实际装配的上下文压力 | 由模型窗口和输出预留决定 |
| 本轮累计 token | 同一 `run_id` 内所有模型调用报告的 input + output token 累计 | `100000` |
| 模型调用 | 同一 `run_id` 的模型调用次数 | `24` |
| 工具调用 | 同一 `run_id` 被模型提出并允许执行的工具调用次数 | `64` |

本轮累计值会因为每次模型调用再次携带历史消息和工具结果而重复计费。因此 Web Search 即使只发生一次，过长的搜索结果也会在后续循环放大 token 消耗。

## 2. H2.5B0 纵向切片

1. `RunBudgetMiddleware` 在新 run 初始化时把三类上限写入 checkpoint-safe state。
2. `HarnessEventAdapter` 从 values stream 投影去重的 `run_budget_updated` 事件。
3. 前端将“本轮预算”与“上下文窗口”分别投影；工作台顶栏优先展示本轮累计值。
4. `search_web` 增加单次证据 `token_budget`，默认 `2000`，允许范围 `256..8000`。
5. Web Search 只向模型返回预算内 title、URL 和 excerpt；超出的结果计入 `omitted_count`。
6. `run_shell` 由自身的 `1..120s` timeout 作为唯一命令超时边界；外层工具 timeout 留出清理窗口。
7. Shell timeout 终止完整进程组，并返回 `shell_timeout`、`retryable=true`。
8. 拦截 `find /` 根目录扫描，以及缺少连接和总时限的 `curl` / `wget`。
9. 系统提示明确公共网页研究使用 `search_web`；不可用时如实报告，不用 shell 网络命令代替。

## 3. 非目标

- 本切片不提高 `max_run_tokens`，也不允许通过 Subagent 绕过父 run 预算。
- 不实现 URL 正文抓取、HTML 主内容抽取、PDF 下载或 Web Artifact。
- 不自动把网页证据写入 Knowledge 或 Memory。
- 不改变现有模型上下文压缩阈值。

## 4. H2.5B 后续边界

下一阶段继续做安全 Fetch + Artifact：URL/IP 双重 SSRF 校验、redirect 重验、响应与解压预算、HTML/PDF allowlist、正文 artifact 化和预算内 excerpt。H2.5B0 的本轮预算事件和 Web 证据预算是 Fetch 的前置门禁，不把两者混进一个大提交。

## 5. 验收

- 运行含多次模型与工具调用的会话，状态画布显示实时本轮 token、模型和工具计数。
- `search_web(top_k=6, token_budget=256)` 返回 evidence，且 `used_tokens <= 256`。
- timeout 命令返回 `shell_timeout`，其后台 child 不会在超时后继续写文件。
- `find /` 和无完整 timeout 的 `curl` 被策略层拒绝；带 `--connect-timeout` 与 `--max-time` 的诊断请求允许执行。
- 后端关联测试、前端全量测试与生产构建、Ruff、mypy、`git diff --check` 全部通过。
