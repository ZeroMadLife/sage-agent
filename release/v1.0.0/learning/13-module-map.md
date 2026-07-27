# 模块速查表：先找责任边界，再找源码入口

Sage 的源码导航可以先压成一句话：不要从目录名猜实现，而要从用户现象找到事实源、运行边界和对应测试。

> Last verified against: `dev/sage-v7@236e07c` (2026-07-23)

![Sage 模块地图](assets/13-module-map.png)

一条最实用的导航路径是：

```text
用户现象
  -> API 或前端入口
  -> Runtime / Harness 边界
  -> Tool / State / Knowledge 责任模块
  -> canonical store
  -> contract test / integration test
```

源码文件会变化，这条责任链不应该随重构失效。

## 先判断问题属于哪一层

| 现象 | 第一责任边界 | 第一入口 |
| --- | --- | --- |
| 消息发不出去、会话建不起来 | API 与 owner/session 校验 | `api/coding.py` |
| run 没启动、没结束或停不下来 | Runtime 生命周期 | `core/coding/runtime.py::CodingRuntime.run_turn` |
| v2 行为和 legacy 不一致 | runtime profile 适配 | `core/harness/runtime_adapter.py::SageHarnessRuntimeAdapter` |
| 工具没执行或被拒绝 | ToolExecutor | `core/coding/tool_executor/executor.py::ToolExecutor.execute` |
| 文件读写范围异常 | Workspace / Sandbox | `core/coding/context/workspace.py::WorkspaceContext.path` |
| 刷新后消息或状态丢失 | Timeline / Transcript | `core/coding/persistence/` |
| Memory、Knowledge 或摘要混在一起 | 状态事实边界 | 对应 store 与 projection |
| UI 显示与 timeline 不一致 | 前端 projection | `frontend/src/harness/timelineProjection.ts` |
| 检索没有 citation | Knowledge retrieval | `core/knowledge/retrieval.py` |
| 说不清改动是否回归 | Tests / Evals | `tests/` 与 `evals/` |

这张表不是完整索引，而是第一跳。第一跳选错，后面的阅读通常都会绕远。

## 六个模块区不是六个文件夹

Sage 可以按责任压成六区：

```text
产品入口：Vue / FastAPI / WebSocket
运行编排：Runtime / Engine / create_agent / Middleware
工具边界：Registry / Executor / Permission / Approval / Sandbox
状态持久：Session / Transcript / Timeline / Checkpoint / Run / Artifact
知识学习：Memory / Knowledge / Retrieval / Proposal / Learning Evidence
测试证据：Contract / API / Integration / Eval / Deployment Gate
```

同一目录可能跨多个区，同一区也可能分布在应用层和通用 package。阅读时要跟责任走，不要跟文件夹走。

## 产品入口只负责接入，不负责重新实现 Runtime

`api/coding.py` 是主入口，但它不应该成为第二个 Agent Engine。这里负责 owner/session 访问控制、session 生命周期、WebSocket、timeline、approval、stop、runtime profile 和应用依赖接线。

如果一个 bug 表现为 HTTP 状态错误、session 404、WebSocket 参数不兼容，先看 API。若 API 已经正确调用 Runtime，就应继续向下追，不要在路由里补业务循环。

## Runtime 和 Engine 解决不同问题

`CodingRuntime` 是 session 的运行现场。它装配模型、workspace、context、stores、tools、permission、approval、run lease 和事件出口，并负责在异常路径上收口。

legacy 的 `Engine` 负责 turn 级循环：请求模型、解析输出、执行工具、继续或终止。它不应该决定 workspace 属于谁，也不应该自行创建持久化根目录。

v2 通过 `SageHarnessRuntimeAdapter` 把 Sage 的 session、timeline、stores 与通用 `sage_harness` package 接起来。`create_sage_agent` 只接受显式参数，不读取 Sage 全局状态。

因此双轨导航要先问：这个 session 的 `runtime_profile` 是什么？

| Profile | 编排入口 | 工具协议 |
| --- | --- | --- |
| legacy | `CodingRuntime` + `Engine` | XML 解析与应用 ToolExecutor |
| `deerflow_v2` | `SageHarnessRuntimeAdapter` + `create_sage_agent` | 原生 tool calling + middleware |

历史 session 不静默迁移。看到行为差异时，先确认 profile，再比较共享契约。

## `sage_harness` package 和 Sage 应用层不能倒置

`packages/sage_harness/sage_harness/` 提供可复用的运行骨架。阅读顺序是 `agents/factory.py`、`config.py`、`state.py`、`middleware/`、`capabilities/`、`deferred_tools.py`、`sandbox/`、`subagents/` 和 `mcp/`。

应用层 `core/harness/` 负责 adapter：把 Knowledge、Memory、Sandbox、MCP、Subagent、事件和 usage 接到这些 ports。

正确依赖方向是应用依赖通用 package。通用 package 不能反向导入 `api/`、Sage 数据库或前端事件模型。

## ToolExecutor 是动作问题的第一站

工具问题不要先从具体工具函数开始。先看执行管线有没有接受这次调用：

```text
tool name
  -> args validation
  -> permission
  -> policy / fresh-read
  -> dangerous command / approval
  -> sandbox or tool implementation
  -> bounded result / artifact / event
```

对应模块是：

| 问题 | 模块 |
| --- | --- |
| 工具是否注册 | `core/coding/tools/registry.py` |
| 参数是否合法 | `core/coding/tools/schemas.py` |
| 是否允许调用 | `tool_executor/permissions.py` |
| 调用是否满足前置条件 | `tool_executor/policy.py` |
| 是否需要人工确认 | `tool_executor/approval.py` |
| 怎样执行和留证 | `tool_executor/executor.py` |
| 文件路径是否越界 | `context/workspace.py` |
| 执行环境是什么 | `core/harness/sandbox_factory.py` |

只有管线放行以后，才需要进入 `file_tools.py`、`shell_tool.py` 或其他具体工具。

## 状态问题先找 canonical store

Sage 没有一个万能数据库。不同事实有不同 store：

| 事实 | Canonical store | 主要消费者 |
| --- | --- | --- |
| 会话元数据与 runtime profile | `CodingSessionStore` | API / Runtime |
| 完整 transcript | `TranscriptStore` | Context / Resume / Export |
| 用户可见事件序列 | `SessionEventJournal` | Timeline / Reconnect / UI |
| graph 执行状态 | LangGraph checkpointer | v2 resume / interrupt |
| run 诊断与 diff | `RunStore` | Inspector / Review |
| 大工具输出 | `ToolResultStore` / Artifact port | Context / UI |
| 压缩结果 | `CompactionStore` | Context projection |
| 长期记忆 | `MemoryStore` | Recall / Proposal / Dream |

看到“刷新后丢了”时，先问丢的是哪一种事实。WebSocket 收到过不等于 journal 已写入；timeline 可重放也不等于 transcript 已经完整；checkpointer 有状态也不等于用户可见终态已经落盘。

## Knowledge 是独立平台，不是 Memory 的大表

`core/knowledge/` 处理来源、解析、proposal、revision、检索、citation、图谱和学习证据。

常见入口是 `KnowledgeStore.ingest`、`KnowledgeStore.approve`、`retrieval.py`、`KnowledgeStore.citation`、`core/knowledge/jobs/`、`core/knowledge/parsing/`、`graph.py` 和 `core/learning/`。

Memory 保存用户确认过的稳定事实和偏好；Knowledge 保存有来源、有 revision、可引用的知识。Context summary 只服务当前任务连续性。三者不能互换。

## 前端问题先区分事实、投影和展示

前端主链可以这样读：

```text
REST / WebSocket event
  -> harness session controller
  -> timeline projection
  -> surface adapter
  -> Chat / Canvas / Details components
```

| 层 | 第一入口 |
| --- | --- |
| 会话连接与恢复 | `frontend/src/harness/useHarnessSession.ts` |
| timeline 投影 | `frontend/src/harness/timelineProjection.ts` |
| surface 契约 | `frontend/src/harness/surfaces/` |
| 共享 Harness UI | `frontend/src/components/harness/` |
| Coding 画布 | `frontend/src/views/CodingView.vue` |
| Knowledge 画布 | `frontend/src/views/KnowledgeView.vue` |
| Assistant 首页 | `frontend/src/views/AssistantHomeView.vue` |
| 类型契约 | `frontend/src/types/api.ts` |

UI 组件不应重新推断 run 是否完成。它应消费 projection 结果。否则刷新、实时流和历史重放会得到三套不同状态。

## 为什么不是生成一份全仓文件清单

文件清单看起来完整，但维护成本高，而且容易把目录结构误当架构。

Sage 更需要的是稳定导航规则：

- 入口层不复制运行逻辑；
- Runtime 不替代 canonical store；
- 通用 package 不反向依赖产品层；
- Tool implementation 不绕过 ToolExecutor；
- projection 不成为新的事实源；
- tests 对准公共契约，不绑定私有调用顺序。

只要这些规则稳定，文件移动以后仍能重新找到正确入口。

## 和 Claude Code / CodeBuddy 的对标

| 维度 | Sage | 对标系统 |
| --- | --- | --- |
| 导航主线 | API、Runtime、Tool、State、Knowledge、Evidence | Claude Code 模块更多，产品控制面与 bridge 更成熟 |
| 通用运行时 | 独立 `sage_harness` package + Sage adapters | Claude Code 有更完整的 SDK、工具和远程运行生态 |
| 研发约束 | 责任边界、测试入口和文档一起维护 | CodeBuddy 强调代码即配置、统一环境和 SDD |
| 状态事实 | 多 store 分权 | 成熟系统同样区分 transcript、task、telemetry 和 UI state |
| 学习可读性 | 模块边界可直接作为学习地图 | 商业系统覆盖更广，但内部实现不一定同等可读 |

Sage 不需要追求“文件和 Claude Code 一一对应”。真正值得对齐的是边界是否可发现、可替换、可测试。

## 最危险的不是找不到文件，而是改错事实源

- 在 WebSocket handler 里修复本应由 journal 保证的顺序；
- 在 Vue 组件里推断本应由 projection 决定的终态；
- 在具体工具里绕过 permission、policy 或 approval；
- 把 checkpoint 当成 transcript，恢复后缺少用户可见记录；
- 把 Memory 当成 Knowledge，写入没有 citation 的长期事实；
- 在 `sage_harness` package 中导入 Sage 应用数据库；
- 只修改实现文件，没有更新对应 contract test。

这些问题短期都可能“修好页面”，长期会让双轨运行时和恢复语义继续分叉。

## 设计文档级补充：模块地图要能指导改动

判断入口是否正确，可以问四个问题：

1. 这个模块拥有事实，还是只消费投影？
2. 这个模块决定策略，还是只执行已决定的动作？
3. 这个模块属于通用 harness，还是 Sage 产品适配？
4. 哪个测试能从公共边界证明改动有效？

### 常见改动路线

| 想改什么 | 阅读顺序 |
| --- | --- |
| 新增工具 | schema -> registry -> executor -> permission/policy -> tests |
| 新增远程能力 | capability -> MCP/Web adapter -> remote marker -> middleware -> tests |
| 修改 run 终态 | Runtime -> journal -> adapter -> projection -> UI tests |
| 修改恢复 | transcript/checkpoint/journal -> Runtime -> API -> frontend replay |
| 修改 Knowledge 写入 | proposal -> policy -> approve -> projection -> retrieval benchmark |
| 修改 Context | source stores -> budget -> projection -> compaction -> cache tests |
| 修改 Sandbox | port -> provider -> deployment policy -> contract -> server smoke |

### 最小验收清单

| 验收点 | 证据 |
| --- | --- |
| 能从现象找到唯一第一责任边界 | 本章导航表与实际 import/call path 一致 |
| 双轨入口没有混淆 | legacy 与 v2 测试分别覆盖，共享契约有 parity test |
| canonical store 清楚 | 文档没有把 UI、WebSocket 或 projection 写成事实源 |
| package 依赖方向正确 | `tests/harness/test_package_boundary.py` |
| ToolExecutor 不被绕过 | tool executor、permission、policy tests |
| 前端重放一致 | timeline projection 与 session tests |
| Knowledge 写入受控 | proposal、approve、revision tests |
| 发布结论有证据 | contract、build、benchmark 或 deployment smoke |

面试里可以这样收束：Sage 的模块地图不是一张目录树，而是一张责任图。用户现象先落到 API、Runtime、Tool、State、Knowledge 或 Evidence，再找到 canonical store 和公共测试。这样重构可以移动文件，但不能悄悄移动事实边界。
