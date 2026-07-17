# Sage DeerFlow Harness 兼容迁移实施计划

> 日期：2026-07-16
>
> 设计依据：`docs/superpowers/specs/2026-07-16-sage-deerflow-harness-migration-design.md`
>
> 上游基线：`bytedance/deer-flow@693507870cae26dadf3af487f811eb2bcfe18f87`
>
> 开发分支：`dev/sage-v7`

## 1. 交付原则

本计划不是把 DeerFlow 仓库复制进 Sage，而是按可验证纵向切片迁移 Harness 深模块。每个 Wave 必须能独立安装、测试、回滚和联调，不能依赖后续 Wave 才恢复现有功能。

固定约束：

1. 保留 Sage Vue、`/api/v1/coding/*`、durable timeline、审批、Knowledge revision/citation 和 workspace diff 契约。
2. `packages/sage_harness` 不得导入 `api.*`、`core.knowledge` 具体存储、Vue 或应用级全局单例。
3. 新 runtime 使用 LangChain `create_agent` 和原生 tool calling；不在新主链继续扩展 XML 协议。
4. 运行时 profile 持久化到 session；历史缺失值解释为 `legacy`，不得静默迁移。
5. 权限、owner、workspace、审批、revision 和持久化写入 fail-closed。
6. 不保存模型私有 chain-of-thought，只投影公开文本、工具、阶段、引用和 Provider 明确提供的 reasoning summary。
7. 删除旧代码必须同时具备零生产引用、历史 replay 兼容、对等矩阵通过和独立删除提交；用户未跟踪文件不属于清理范围。

## 2. 提交与验证策略

每个可独立验收的小版本执行：

```text
定向测试
  -> 后端全量 pytest
  -> ruff + mypy
  -> 前端全量测试 + build（触及共享事件/UI 时）
  -> git diff --check
  -> API/store/权限审查
  -> 浏览器 live/replay/refresh（有用户可见行为时）
  -> 更新 sage-learning
  -> 提交到 dev/sage-v7
```

不会把 Wave 0–6 压成一个提交。上游实质复制文件时新增 `THIRD_PARTY_NOTICES.md`；只使用架构模式或公开接口时不伪造源码 provenance。

## 3. Wave 0：Python 3.12 与依赖边界

### Task 0.1：建立可复现 Python 3.12 基线

**修改：**

- `requirements.txt`
- `pyproject.toml`
- 新增 `.python-version`
- `README.md`
- `docs/GETTING-STARTED.md`
- 新增 `.github/workflows/backend-quality.yml`

**步骤：**

1. 固定 Python 3.12，升级 LangChain/LangGraph/Provider/MCP adapter 到已审计候选版本。
2. 显式加入 SQLAlchemy async 所需 `greenlet`。
3. 删除无生产入口的 Mem0/Qdrant/sentence-transformers 具体实现和发布依赖，保留 provider-neutral memory port。
4. CI 从干净环境安装核心依赖并执行 pytest、ruff、mypy；不得依赖开发机已安装包。
5. 文档启动命令改为 Python 3.12，并解释旧 Conda 环境不可继续混装。

**验证：**

```bash
uv venv --python 3.12 /tmp/sage-wave0
uv pip install --python /tmp/sage-wave0/bin/python -r requirements.txt
PYTHONPATH=. /tmp/sage-wave0/bin/python -m pytest -q
/tmp/sage-wave0/bin/python -m ruff check .
PYTHONPATH=. /tmp/sage-wave0/bin/python -m mypy core/ mcp_servers/ agents/ api/ db/
```

### Task 0.2：删除废弃 Mem0 实现并保留通用 memory seam

**修改：**

- 删除 `core/memory/mem0_factory.py`、`core/memory/long_term.py` 和 `scripts/demo_memory.py`
- `core/memory/extractor.py`
- `agents/memory_node.py`
- 对应通用 port 测试

**行为：**

- 发布环境不再安装或启动 Mem0/Qdrant，不保留无法在新依赖基线上运行的演示入口。
- 旅行图仍接受符合 `LongTermMemoryPort` 的外部实现，`MemoryManager` 不依赖具体 SDK。
- 未来接入任何第三方 memory provider 时必须进入独立 adapter 和真实外部 smoke，不混入 Harness 核心包。

### Task 0.3：建立 Harness 包和依赖防火墙

**新增：**

- `packages/sage_harness/pyproject.toml`
- `packages/sage_harness/sage_harness/__init__.py`
- `packages/sage_harness/sage_harness/ports.py`
- `tests/harness/test_package_boundary.py`
- `tests/harness/test_dependency_baseline.py`

**行为：**

- 包可独立 editable install。
- AST 边界测试禁止反向依赖应用层。
- Port 只定义 protocol/value object，不依赖 Sage concrete store。

**Wave 0 提交边界：** 用户可见行为不变；核心安装集干净、全量后端门禁通过。

## 4. Wave 1：Harness Skeleton 与只读真实运行

### Task 1.1：SageThreadState 与 reducer

**新增：**

- `packages/sage_harness/sage_harness/state.py`
- `tests/harness/test_thread_state.py`

实现 messages、artifacts、todos、goal、delegations、skill refs、summary、memory refs 和 approval context。测试显式覆盖 `None` 保留、终态不可降级、稳定 ID 去重、上限裁剪和非法冲突。

### Task 1.2：Agent factory 与 middleware registry

**新增：**

- `packages/sage_harness/sage_harness/agents/factory.py`
- `packages/sage_harness/sage_harness/middleware/registry.py`
- `packages/sage_harness/sage_harness/config.py`
- `tests/harness/test_agent_factory.py`
- `tests/harness/test_middleware_order.py`

先交付最小链：input sanitization、thread context、provider error、tool error、loop/token budget、terminal response。middleware 顺序通过调用轨迹测试锁定。

### Task 1.3：runtime profile 与持久化兼容

**修改：**

- `core/coding/runtime.py`
- `core/coding/persistence/session_store.py`
- `api/schemas.py`
- `api/coding.py`
- `frontend/src/types/api.ts`
- `frontend/src/stores/coding.ts`
- 对应 API/store/frontend tests

新增服务端受控 `runtime_profile: legacy | deerflow_v2`。历史缺失值为 `legacy`；run 开始后不可切换；catalog 只在 capability 开启时允许显式创建新 profile。

### Task 1.4：RunManager、checkpointer 与事件适配

**新增：**

- `packages/sage_harness/sage_harness/runtime/manager.py`
- `packages/sage_harness/sage_harness/runtime/checkpoint.py`
- `packages/sage_harness/sage_harness/runtime/events.py`
- `core/harness/runtime_adapter.py`
- `core/harness/event_adapter.py`
- `tests/harness/test_run_manager.py`
- `tests/core/harness/test_event_adapter.py`

以 LangGraph checkpointer 保存执行状态，以 Sage SessionEventJournal 保存用户可见事实。事件用 `run_id + source_event_id` 幂等；关键事件先持久化再广播。

### Task 1.5：只读工具的原生循环

将现有 `list_files` / `read_file` 通过 adapter 转为 LangChain Tool，完成一个真实场景：用户提问 -> 多次只读工具 -> tool result -> 最终流式正文。浏览器验证 live、刷新 replay 和无重复消息。

## 5. Wave 2：Policy、审批与完整 Coding Tools

### Task 2.1：工具 metadata 和 adapter

**新增/修改：**

- `packages/sage_harness/sage_harness/tools/metadata.py`
- `packages/sage_harness/sage_harness/tools/registry.py`
- `core/harness/tool_adapters.py`
- `core/coding/tools/*`

统一 risk、permission、surface、remote content、output/artifact policy 和 idempotency。现有工具实现继续作为 concrete executor，避免第一轮重写文件与 Shell 逻辑。

### Task 2.2：策略中间件

实现 input/remote content sanitization、tool output budget、Sage policy、read-before-write、tool progress/error、loop 和 token/step budget。每个 middleware 明确 state channel、fail mode 和 timeline event。

### Task 2.3：审批 interrupt/resume

**新增/修改：**

- `packages/sage_harness/sage_harness/middleware/approval.py`
- `core/harness/approval_adapter.py`
- `api/coding.py`
- `core/coding/tool_executor/approval.py`
- `frontend/src/components/coding/chat/CodingApprovalCard.vue`

批准绑定 session/run/tool_call/args digest。客户端只提交决策，服务端构造 `Command(resume=...)`。覆盖 once、session、reject、参数改变、刷新、进程恢复和重复提交。

### Task 2.4：Diff、artifact 与终态

写文件、patch、Shell 和长输出接回现有 diff/artifact；成功 run 必须有 assistant 终答或明确结构化终态。验证工具失败后修正参数继续循环。

## 6. Wave 3：Skills、MCP 与 Sandbox

### Task 3.1：LocalWorkspaceSandbox Port

**新增：**

- `packages/sage_harness/sage_harness/sandbox/base.py`
- `core/harness/local_sandbox.py`
- `tests/harness/test_sandbox_contract.py`

绑定现有 workspace path policy。开发环境允许 local；生产配置禁止任意 host root。预留 `ContainerSandbox`，不把未实现远程隔离伪装成已交付。

### Task 3.2：Skill 激活与 deferred tool catalog

复用现有 Skill Registry，state 只保存 name/path/version/description 引用。`tool_search` 返回目录摘要，按需提升完整 schema；工具名冲突和 capability 过滤 fail-closed。

### Task 3.3：MCP Manager

**新增/修改：**

- `packages/sage_harness/sage_harness/mcp/manager.py`
- `packages/sage_harness/sage_harness/mcp/deferred.py`
- `core/harness/mcp_adapter.py`
- 现有 MCP config/API tests

负责 stdio/streamable HTTP 生命周期、缓存失效、前缀、OAuth 服务端解析和 remote content 标记。验证 MCP 搜索、提升、执行、断线、超时和错误归一。

### Task 3.4：服务器 Container Sandbox

实现可部署的容器隔离或经过安全评审的等价方案，并提供健康检查、资源上限、workspace mount、清理和 orphan reconciliation。未通过该门禁前不得把新 runtime 设为生产默认。

## 7. Wave 4：Subagents

### Task 4.1：注册表、状态与 `task` 工具

**新增：**

- `packages/sage_harness/sage_harness/subagents/registry.py`
- `packages/sage_harness/sage_harness/subagents/executor.py`
- `packages/sage_harness/sage_harness/subagents/events.py`
- `tests/harness/test_subagent_executor.py`

子代理 capability 默认最小化，显式继承 workspace/tool/token/timeout。结果以受限 ToolMessage 返回，完整过程写 child run。

### Task 4.2：并发、总量、超时和取消

实现 per-run concurrency、total delegations、递归深度、timeout、父取消传播和 checkpoint 隔离。覆盖成功、失败、超时、取消、预算耗尽和父运行恢复。

### Task 4.3：timeline 与 Vue 展示

扩展事件 projection 和 Coding Run Trace，展示父子路径、状态、耗时、工具摘要和结果引用；默认折叠，不把长 child transcript 全量塞进主消息。

## 8. Wave 5：Durable Context、压缩、Memory 与 Knowledge

### Task 5.1：durable context middleware

投影冻结 surface context、goal、todo、delegation、active skill refs、summary 和 memory refs。所有内容带不可信边界和 token budget。

### Task 5.2：自动/手动压缩统一

保护当前请求、未完成 tool call、pending approval 和近期消息；摘要进入 `summary_text` channel，手动压缩继续生成 Sage artifact。覆盖 checkpoint resume 和压缩后继续工具循环。

### Task 5.3：proposal-only memory

接入 Sage Durable Memory Port。模型只能提出候选事实，证据、风险和确认规则仍由 Sage adapter 决定；不能通过 middleware 直接持久化高风险偏好。

### Task 5.4：Knowledge retrieval/citation/revision

通过 `KnowledgePort` 调用现有 search，按 token budget 返回 evidence 和 citation ID。回答保留 revision-bound citation；revision 失效、跨 workspace/owner 和未经确认学习均有回归测试。

## 9. Wave 6：默认切换、性能门禁与删旧代码

### Task 6.1：对等场景与指标

建立 legacy / deerflow_v2 同题矩阵：首 token、多工具、失败恢复、审批、压缩、MCP、子代理、Knowledge、预算和隔离。记录完成率、工具成功率、policy compliance、P50/P95 首 token 与总耗时。

### Task 6.2：默认值与回滚

仅当设计中的切换门禁全部通过，才让新 session 默认 `deerflow_v2`。已有 session profile 不变，并保留服务端 feature flag 回滚新建默认值。

### Task 6.3：删除审计

对旧 Engine/XML parser/legacy adapters 执行：

1. `rg` 生产引用与动态入口审计。
2. 历史 session/replay fixture 审计。
3. 旧 profile 使用量和恢复能力审计。
4. 对等矩阵与浏览器回归。
5. 只删除已经被新 runtime 覆盖的写路径；必要的历史反序列化器保留为 read-only compatibility。

删除必须是独立 commit，提交前后分别跑全量门禁。不得以“Git 可回滚”为理由删除仍有运行契约的代码。

### Task 6.4：发布收口

- 更新 README、架构图、运维和故障恢复文档。
- 更新 Obsidian `sage-learning`：每 Wave source commit、测试、风险和遗留边界。
- 后端、前端、浏览器、服务器部署和 `git diff --check` 全部通过。
- 工作区只允许明确列出的用户未跟踪文件；所有发布源码改动均已提交。

## 10. 完成审计矩阵

| 要求 | 权威证据 |
|---|---|
| Python/LangChain/LangGraph 新基线 | 干净环境安装日志、全量 pytest、ruff、mypy、CI |
| 独立 Harness 包 | wheel/editable install、AST firewall、包测试 |
| 原生多步循环 | create_agent 集成测试、真实模型浏览器 run |
| 审批与策略 | once/session/reject/resume、安全负例 |
| durable timeline | live/replay/refresh/restart、幂等与恢复测试 |
| MCP/Sandbox | 生命周期测试、remote content 策略、服务器隔离 smoke |
| Subagents | 父子状态、限制、取消、checkpoint、Vue 展示 |
| 压缩与记忆 | 压缩后继续、proposal gate、跨 session 恢复 |
| Knowledge | citation/revision/owner/workspace/确认门测试 |
| 默认切换与删旧代码 | 对等指标、profile 回滚、零生产引用审计 |
| 发布干净 | 所有 Wave commit、Obsidian 收口、干净 tracked worktree |

只有矩阵每项都有当前状态证据，才能关闭迁移 Goal。
