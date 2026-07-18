# Harness 2.0 联调与验收手册

本文把 Harness 联调从“随便聊几句”改成可重复的用户验收矩阵。每次小版本先跑确定性测试，再用真实页面验证一条最小纵向链路；只有涉及 Provider、MCP 或 Web 时才使用低权限真实凭据。

## 1. 五层联调策略

| 层级 | 目的 | 通过标准 |
| --- | --- | --- |
| Contract | 验证事件、API、权限和持久化契约 | 定向 pytest 全绿，不访问外网 |
| Deterministic runtime | 用脚本化模型走完整 Agent loop | tool、approval、resume、terminal 顺序稳定 |
| Browser smoke | 站在普通用户视角操作主对话 | 流式正文、状态流转、工具摘要、审批、自动滚动正常 |
| Fault injection | 主动制造拒绝、断线、刷新和后端重启 | 不越权、不重复执行、可恢复或明确失败 |
| Canary | 使用低权限真实 Provider/MCP/Web | 无密钥回显，引用和远程内容策略生效 |

不要用一次“大而全”的 Prompt 同时验所有能力。一次只验证一个变量，失败时才能判断是模型、Harness、工具、权限、前端投影还是外部服务的问题。

## 2. 启动与基线

当前本地联调可使用独立端口，避免占用默认开发栈：

```bash
PATH="/tmp/sage-h2-4a-venv-0717/bin:$PATH" \
PYTHONPATH="packages/sage_harness:." \
SAGE_SKIP_DOCKER=1 \
BACKEND_PORT=8002 \
FRONTEND_PORT=5182 \
bash scripts/dev.sh
```

先确认服务和 V2 会话：

```bash
curl -fsS http://127.0.0.1:8002/health

curl -fsS -X POST http://127.0.0.1:8002/api/v1/coding/session \
  -H 'content-type: application/json' \
  -d '{"runtime_profile":"deerflow_v2","approval_policy":"ask"}'
```

打开 `http://127.0.0.1:5182/#/coding`，确认新会话标记为 Harness 2.0。联调期间同时观察浏览器 Network/Console 和后端终端；不要只根据最终回答判断是否通过。

## 3. 用户 Prompt 矩阵

以下 Prompt 可以直接粘贴。每条使用新会话，或在记录中注明前置状态。

| 编号 | 用户 Prompt | 主要观察点 |
| --- | --- | --- |
| F1 快速回答 | `只用一句话回答：2 + 2 等于多少？不要调用工具。` | 首 token 快；无伪造工具或 reasoning；正常 terminal |
| F2 只读目录 | `查看当前工作区根目录，只列出前 5 个文件或目录，并告诉我总共看到了多少项。不要修改文件。` | 接收目标 -> 组装上下文 -> 规划 -> 工具 -> 回答；工具卡显示命令摘要和耗时 |
| F3 只读文件 | `读取 README.md 的第一段，用两句话概括项目定位，不要读取其他文件。` | 工具参数正确；结果可展开；回答基于真实内容 |
| F4 审批拒绝 | `在 tmp/harness-smoke.txt 写入内容 smoke-denied。` | 出现审批；选择拒绝后文件不存在；时间线保留拒绝和失败摘要 |
| F5 审批一次 | `在 tmp/harness-smoke.txt 写入内容 smoke-approved，并确认写入结果。` | 选择“允许一次”；只执行一次；Diff 能定位到本轮变更 |
| F6 Deferred 能力 | `查看当前会话的待办事项；如果能力还没有加载，先查找再使用。` | 先 `tool_search`，后目标工具；出现 catalog/selection 审计；未提升能力不能执行 |
| F7 Skill 约束 | `/review 只审查 README.md，禁止修改文件，也不要执行 shell。` | Skill 激活；模型可见工具和执行层都受 `allowed_tools` 约束 |
| F8 子 Agent | `请让只读 Explore 子 Agent 比较 README.md 与 docs/GETTING-STARTED.md 的启动步骤，只返回差异摘要，不要修改文件。` | child started/terminal 可追溯；父运行等待结果；点击子运行可打开完整 child timeline；子 Agent 不获得写权限 |
| F9 Knowledge RAG | `根据知识库说明 Harness 2.0 的目标，并给出可点击引用；没有证据就明确说没有。` | 只使用 revision-bound citation；引用能回到来源；无证据不编造 |
| F10 恢复 | `读取 README.md 后给出三点摘要。` 在工具运行或流式输出时刷新页面 | 历史 replay 不重复；活动 run 恢复；同一工具不二次执行 |
| F11 Web Search | `只搜索 LangGraph checkpoint 的官方文档，比较 thread 恢复与 checkpoint 恢复，给出每条结论的网页引用；不要保存到知识库。` | 先发现并提升 `web:search`；只返回 HTTPS 证据和稳定 `wcite_` 引用；不读取正文、不写 Knowledge |
| F12 断连草稿 | 停止后端后输入 `连接恢复后只回答：消息没有丢失。` 并按回车 | 输入框保留原文；显示“连接正在恢复”；连接成功后由用户再次发送，不能静默丢消息或重复提交 |

MCP 只在已配置低权限测试服务时追加：

```text
使用已配置的 MCP 搜索杭州西湖的测试资料，只返回标题和来源；不要保存到知识库。
```

通过标准：先看到 schema-free discovery，再按 stable capability ID 提升；远程内容被标记为不可信，服务端错误不回显连接串或凭据。

## 4. 故障注入

| 场景 | 操作 | 预期 |
| --- | --- | --- |
| 审批拒绝 | 对 F4 选择“拒绝” | 工具不执行，失败可追溯，回答不宣称成功 |
| 浏览器断线 | 运行中刷新或断开 Network | durable timeline 补齐缺口，不重复 tool call |
| 发送时断线 | 停止后端后输入消息并按回车 | 草稿保留，明确显示恢复提示；不创建幽灵 run |
| 后端重启 | 停止 8002 后用同一命令重启 | 已完成 run 可 replay；待审批 run 可恢复或明确标记 interrupted |
| 过期 promotion | 能力提升后刷新 MCP/Skill revision | 旧 catalog hash 失效，调用 fail-closed |
| 外部服务不可用 | 临时停用测试 MCP | 主回答说明不可用，不泄露异常栈和连接配置 |
| 长输出 | 运行会产生长列表的只读命令 | 主时间线保留 preview 和 artifact ref，不全量撑开页面 |

## 5. 审计与健康检查

获取当前会话可见能力：

```bash
curl -fsS 'http://127.0.0.1:8002/api/v1/harness/capabilities?session_id=<SESSION_ID>&surface=coding'
```

获取最近 30 天的内容无关健康指标：

```bash
curl -fsS 'http://127.0.0.1:8002/api/v1/harness/capabilities/health?session_id=<SESSION_ID>&surface=coding&range=30d'
```

健康响应只能包含 stable capability ID、当前 revision/availability、调用次数、首次/最近成功、P50/P95 和固定失败类别。不得出现 Prompt、工具参数、工具结果、绝对 home path、完整 Tool Schema、异常文本或凭据。

Timeline 中重点检查：

```text
capability_catalog_updated
capability_selected | capability_selection_failed
capability_invocation_completed
tool_call -> tool_result
subagent_started(operation_ref) -> child run timeline -> subagent_terminal
approval_required -> approval resolution
assistant text_delta -> final -> terminal
```

## 6. 验收记录模板

```text
Commit / PR:
环境与端口:
模型 / Provider:
会话 ID:

F1  快速回答          PASS / FAIL  证据:
F2  只读目录          PASS / FAIL  证据:
F4  审批拒绝          PASS / FAIL  证据:
F5  审批一次          PASS / FAIL  证据:
F6  Deferred 能力     PASS / FAIL  证据:
F7  Skill 约束        PASS / FAIL  证据:
F8  子 Agent          PASS / FAIL  证据:
F9  Knowledge 引用    PASS / FAIL  证据:
F10 刷新/恢复         PASS / FAIL  证据:
F11 Web Search        PASS / FAIL  证据:
F12 断连草稿          PASS / FAIL  证据:

Console error:
后端异常:
未关闭风险:
结论: 可提交 / 继续开发 / 需修复
```

完成后删除专用 smoke 文件；不要清理用户已有的 `tmp/`、未跟踪文件或其他会话工作树。
