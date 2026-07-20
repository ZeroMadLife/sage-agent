# 13 - 模块速查表

> Last verified against: `dev/sage-v7@1009e53` (2026-07-20)

> 遇到问题先按"现象"找模块，再打开入口符号和对应测试。不要从 `__init__.py` 顺序翻文件。

## 后端 Harness

| 问题/组件 | 源码 | 第一入口 | 主要测试 |
| --- | --- | --- | --- |
| Runtime 组装（legacy） | `core/coding/runtime.py` | `CodingRuntime.__init__` | `test_runtime_run_lifecycle.py` |
| Run 生命周期 | `core/coding/runtime.py` | `CodingRuntime.run_turn` | `test_runtime_run_lifecycle.py` |
| Runtime 适配（v2） | `core/harness/runtime_adapter.py` | `SageHarnessRuntimeAdapter` | `test_harness_runtime_adapter.py` |
| Agent 工厂（v2） | `packages/sage_harness/sage_harness/agents/factory.py` | `create_sage_agent` | `test_agent_factory.py` |
| Agent 循环（legacy） | `core/coding/engine/engine.py` | `Engine.run_turn` | `test_engine.py`、`test_agent_loop.py` |
| 模型输出解析 | `core/coding/engine/model_output.py` | `parse` | `test_model_output.py` |
| Typed events | `core/coding/engine/events.py` | `RunEventBase` | `test_events.py` |
| Middleware 链（v2） | `packages/sage_harness/sage_harness/middleware/registry.py` | 默认 8 个 middleware | `test_middleware_order.py` |
| Prompt 构建 | `core/coding/context/manager.py` | `ContextManager.build` | `test_context_compact.py` |
| Token 压力分级 | `core/coding/context/budget.py` | `ContextPolicy` | `test_context_budget.py` |
| 不可变投影 | `core/coding/context/projection.py` | `ContextProjector` | `test_context_projection.py` |
| 结构化压缩 | `core/coding/context/compact.py` | `CompactManager` | `test_context_compactor.py` |
| Workspace 路径 | `core/coding/context/workspace.py` | `WorkspaceContext.path` | `test_workspace.py` |
| Run Diff | `core/coding/context/workspace_diff.py` | `WorkspaceDiffTracker` | `test_workspace_diff.py` |
| Tool 抽象 | `core/coding/tools/base.py` | `RegisteredTool` | `test_tools.py` |
| Tool registry | `core/coding/tools/registry.py` | `build_tool_registry` | `test_tools.py` |
| 参数 schema | `core/coding/tools/schemas.py` | 各 `*Args` | `test_tools.py` |
| 文件工具 | `core/coding/tools/file_tools.py` | `read_file/patch_file` | `test_tools.py` |
| Shell 工具 | `core/coding/tools/shell_tool.py` | `run_shell` | `test_tools.py` |
| Memory 工具 | `core/coding/tools/memory_tools.py` | `remember/dream` | `test_memory.py` |
| Knowledge 工具 | `core/coding/tools/knowledge_tools.py` | `knowledge_search/learn` | `test_knowledge.py` |
| Agent 工具 | `core/coding/tools/agent_tools.py` | `agent/send_message/task_stop` | `test_tools.py` |
| Travel 工具 | `core/coding/tools/travel_tools.py` | `generate_itinerary` | `test_tools.py` |
| Tool 执行管线 | `core/coding/tool_executor/executor.py` | `ToolExecutor.execute` | `test_tool_executor.py` |
| 权限模式 | `core/coding/tool_executor/permissions.py` | `PermissionChecker.check` | `test_permissions.py` |
| 使用策略 | `core/coding/tool_executor/policy.py` | `ToolPolicyChecker.check` | `test_tool_executor.py` |
| 审批与危险命令 | `core/coding/tool_executor/approval.py` | `ApprovalManager` | `test_approval.py` |
| Skill 解析 | `core/coding/skills/skill.py` | `discover_skills` | `test_skills.py` |
| Slash resolve | `core/coding/skills/registry.py` | `SkillRegistry.resolve` | `test_skills.py` |
| Working memory | `core/coding/memory/working.py` | `WorkingMemory.from_session` | `test_memory.py` |
| Durable memory（旧） | `core/coding/memory/durable.py` | `DurableMemory` | `test_memory.py` |
| Memory 组合 | `core/coding/memory/manager.py` | `MemoryManager` | `test_memory.py` |
| Memory store（新 SQLite） | `core/coding/persistence/memory_store.py` | `MemoryStore` | `test_memory_store.py` |
| Session 保存 | `core/coding/persistence/session_store.py` | `CodingSessionStore` | `test_session_store.py` |
| Run trace/timeline | `core/coding/persistence/run_store.py` | `RunStore` | `test_run_store.py` |
| Session events | `core/coding/persistence/session_events.py` | `SessionEventBus.emit` | runtime persistence test |
| Transcript（SQLite） | `core/coding/persistence/transcript_store.py` | `TranscriptStore` | `test_transcript_store.py` |
| Tool result archive | `core/coding/persistence/tool_result_store.py` | `ToolResultStore` | `test_tool_result_store.py` |
| Todo | `core/coding/persistence/todo_ledger.py` | `TodoLedger` | todo runtime test |
| Compaction store | `core/coding/persistence/compaction_store.py` | `CompactionStore` | `test_compaction_store.py` |
| Plan mode | `core/coding/plan_mode.py` | `PlanModeManager` | `test_plan_review.py` |
| Plan approval | `core/coding/plan_review.py` | `PlanReviewManager` | `test_plan_review.py` |
| Worker 管理（legacy） | `core/coding/multiagent/manager.py` | `WorkerManager` | `test_worker_runtime.py` |
| Worker runtime | `core/coding/multiagent/runtime.py` | `run_worker_task` | `test_worker_runtime.py` |
| Run coordinator | `core/coding/run_coordinator.py` | `RunCoordinator` | `test_run_coordinator.py` |
| Knowledge store | `core/knowledge/store.py` | `KnowledgeStore` | `test_store.py` |
| Knowledge retrieval | `core/knowledge/retrieval.py` | `retrieve` | `test_retrieval.py` |
| Knowledge proposals | `core/knowledge/source_proposals/` | `KnowledgeSourceProposal` | `source_proposals/test_service.py` |
| Evidence bundle | `core/harness/evidence_bundle.py` | `CodingEvidenceBundlePort` | `test_evidence_bundle.py` |
| 子代理 adapter | `core/harness/subagent_adapter.py` | `build_subagent_capability` | `test_subagent_adapter.py` |
| Container sandbox | `core/harness/container_sandbox.py` | `ContainerSandbox` | `test_sandbox_contract.py` |
| Local sandbox | `core/harness/local_sandbox.py` | `LocalWorkspaceSandbox` | `test_sandbox_contract.py` |
| MCP adapter | `core/harness/mcp_adapter.py` | `McpAdapter` | `test_mcp_adapter.py` |
| Event adapter | `core/harness/event_adapter.py` | `HarnessEventAdapter` | `test_harness_runtime_adapter.py` |
| Memory adapter | `core/harness/memory_adapter.py` | `MemoryAdapter` | `test_memory_adapter.py` |
| Knowledge adapter | `core/harness/knowledge_adapter.py` | `KnowledgeAdapter` | `test_knowledge_adapter.py` |
| Cloud auth | `core/cloud/auth/repository.py` | `CloudAuthRepository` | `auth/test_repository.py` |
| GitHub OAuth | `core/cloud/github/oauth.py` | `GitHubOAuth` | `test_cloud_github_oauth_routes.py` |
| Secret cipher | `core/cloud/security.py` | `CloudSecretCipher` | `test_cloud_model_provider_routes.py` |

## API

| 现象 | 源码入口 |
| --- | --- |
| 创建/恢复 session | `api/coding.py::create_coding_session/resume_coding_session` |
| WebSocket 流 | `api/coding.py::coding_stream` |
| Timeline REST | `api/coding.py::get_coding_timeline` |
| Timeline WS | `api/coding.py::coding_stream`（支持 `after` 参数） |
| 文件和 Git | `list_coding_files/read_coding_file/coding_git_status` |
| Tool approval | `coding_pending_approval/coding_approval_respond` |
| Stop | `stop_coding_run` |
| Plan approval | `approve_plan/reject_plan` |
| Memory proposal | `approve_memory_proposal/reject_memory_proposal` |
| Runs/Diff | `list_coding_runs/get_coding_run/get_coding_run_diff` |
| Model/mode | `switch_coding_model/switch_permission_mode` |
| Skills/MCP | `list_coding_skills/get_coding_skill/list_coding_mcp_servers` |
| Context | `get_coding_context/compact_coding_context` |
| Cloud auth | `api/cloud_auth.py::github_oauth_start/callback/logout` |
| Cloud workspace | `api/cloud_workspaces.py` |
| Knowledge | `api/knowledge.py` |

API 测试主入口：`tests/api/test_coding_routes.py`。

## 前端

| 组件 | 源码 | 职责 |
| --- | --- | --- |
| Assistant 首页 | `frontend/src/views/AssistantHomeView.vue` | today + composer + 摘要 |
| Coding 页面 | `frontend/src/views/CodingView.vue` | 三栏编排、drawer、滚动 |
| Knowledge 视图 | `frontend/src/views/KnowledgeView.vue` | 知识库管理 |
| Store | `frontend/src/stores/coding.ts` | 状态、REST、stream orchestration |
| Timeline store | `frontend/src/stores/codingTimeline.ts` | 事件投影成 TimelineTurn |
| Event reducer | `frontend/src/stores/codingEvents.ts` | event -> state/effect |
| WebSocket | `frontend/src/stores/codingStream.ts` | 连接、代际、防竞态 |
| API client | `frontend/src/api/coding.ts` | REST URL 和类型化请求 |
| Event/API types | `frontend/src/types/api.ts` | 前端契约 |
| 路由 | `frontend/src/router/index.ts` | URL 深链接 |
| 偏好 | `frontend/src/composables/useWorkbenchPreferences.ts` | localStorage 持久化 |
| 侧栏 | `components/coding/sidebar/CodingSidebar.vue` | sessions/skills/memory/runs/MCP |
| 输入框 | `components/coding/composer/CodingComposer.vue` | send/stop/skill/mode |
| Tool activity | `components/coding/chat/CodingToolActivity.vue` | 工具状态与输出 |
| Approval | `components/coding/chat/CodingApprovalCard.vue` | 工具审批 |
| Plan review | `components/coding/chat/CodingPlanApproval.vue` | plan 审批 |
| Inspector | `components/coding/inspector/CodingInspector.vue` | 文件/变更/运行/记忆四 tab |
| Diff | `components/coding/files/CodingDiffDrawer.vue` | run diff |
| File tree | `components/coding/files/CodingFileTree.vue` | workspace 浏览 |
| Git badge | `components/coding/files/CodingGitBadge.vue` | branch/dirty count |
| Memory panel | `components/coding/memory/CodingMemoryPanel.vue` | memory 事实与 proposal |

## sage_harness 包

| 模块 | 源码 | 职责 |
| --- | --- | --- |
| Ports | `packages/sage_harness/sage_harness/ports.py` | 25+ Protocol/value object |
| State | `packages/sage_harness/sage_harness/state.py` | SageThreadState + reducer |
| Config | `packages/sage_harness/sage_harness/config.py` | HarnessConfig + HarnessRunContext |
| Agent factory | `packages/sage_harness/sage_harness/agents/factory.py` | create_sage_agent |
| Runtime manager | `packages/sage_harness/sage_harness/runtime/manager.py` | HarnessRunManager |
| Middleware registry | `packages/sage_harness/sage_harness/middleware/registry.py` | MiddlewareRegistry |
| Builtin middleware | `packages/sage_harness/sage_harness/middleware/builtin.py` | 默认 registry 所用实现 |
| Durable context | `packages/sage_harness/sage_harness/middleware/durable_context.py` | DurableContextMiddleware |
| Capability registry | `packages/sage_harness/sage_harness/capabilities/registry.py` | CapabilityRegistry |
| Deferred tools | `packages/sage_harness/sage_harness/deferred_tools.py` | 按需暴露工具 |
| Sandbox base | `packages/sage_harness/sage_harness/sandbox/base.py` | SandboxPort Protocol |
| MCP manager | `packages/sage_harness/sage_harness/mcp/manager.py` | McpManager |
| Subagent contracts | `packages/sage_harness/sage_harness/subagents/contracts.py` | AgentProfile |
| Subagent tool | `packages/sage_harness/sage_harness/subagents/tool.py` | task 工具 |
| Subagent middleware | `packages/sage_harness/sage_harness/subagents/middleware.py` | SubagentLifecycleMiddleware |
| Capabilities | `packages/sage_harness/sage_harness/capabilities/` | capability registry |

## Benchmark

| 文件 | 职责 |
| --- | --- |
| `evals/coding/scenarios.py` | 十个场景定义 |
| `evals/coding/runner.py` | 构造 Runtime、运行场景、写报告 |
| `evals/coding/assertions.py` | 文件、事件、policy、memory 断言 |
| `evals/coding/metrics.py` | 聚合指标 |
| `evals/coding/report.py` | HTML 报告 |
| `tests/evals/test_benchmark.py` | Benchmark 自身的回归测试 |
| `evals/knowledge_golden_queries.json` | Knowledge 检索 golden queries |

## 常见问题快速定位

| 症状 | 先看 |
| --- | --- |
| 页面一直 thinking | `run_finished` 是否到 reducer |
| 工具没有执行 | ToolResult 的 permission/policy 字段 |
| 写文件被拒 | fresh read、permission mode、write scope |
| Diff 为空 | snapshot 时机和 ignored rules |
| Resume 后工具消失 | timeline 投影 + `loadSessionMessages` 映射 |
| Memory 没召回 | workspace ID、MEMORY.md、context budget |
| Stop 无效 | active run ID、Engine/Executor stop checkpoint |
| Worker 停不下来 | `WorkerManager.stop` 与 Engine should_stop 未绑定 |
| 旧 session 事件污染新页面 | `CodingStream.generation` |
| 切换会话丢工具调用 | timeline.sqlite3 + codingTimeline.ts 投影 |
| 审批丢失 | durable interrupt 未实现，重启会丢 pending |
| prompt injection | InputSanitizationMiddleware / RemoteContentSanitizationMiddleware |
| 路径逃逸 | WorkspaceContext.path + O_NOFOLLOW |
| 多用户看到别人数据 | Project/Workspace ownership + opaque ID |

## 不要混淆的几套系统

| 系统 | 回答的问题 | 当前阶段 | 源码 |
| --- | --- | --- | --- |
| Durable Memory | 用户明确要求 Sage 长期记住什么 | V7 SQLite revisioned | `core/coding/memory/` |
| Knowledge 检索 | 当前问题应该检索哪些知识片段 | SQLite FTS5 + hashing baseline + RRF | `core/knowledge/` |
| Context Summary | 当前任务怎么接着干 | V7 结构化 compaction | `core/coding/context/compact.py` |
| AST 知识图谱 | 类、函数、文件之间如何结构化连接 | 设计方向，未交付 | - |
| Learning State | 用户对某个知识点掌握到什么程度 | V7 Practice profile 候选 | `core/knowledge/understanding.py` |

Memory 不是 RAG，RAG 也不是知识图谱，Context Summary 不是 Memory。它们生命周期、信任等级、写入语义都不同。

## 版本边界速查

| 版本 | 目标 | 必须完成 | 明确不做 |
| --- | --- | --- | --- |
| V7 Beta | 本地使用与受控私测 | onboarding / 受控来源导入 / 生产 Sandbox 门禁 | 无条件公网开放 |
| 公网候选版 | 受邀用户云 workspace | auth / tenant scope / sandbox / quota / recovery | 读取用户未授权的本地改动 |
| 后续设计 | 本地/云混合代码智能 | Local Companion / Code RAG / AST graph | 将 LLM 推断边冒充 AST 事实 |
