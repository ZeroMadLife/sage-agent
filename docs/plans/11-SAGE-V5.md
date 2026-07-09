# Sage v5.0 架构重构落地记录

> 日期：2026-07-09  
> 分支：`dev/sage-v4`  
> 目标：在行为不变的前提下，完成 `core/coding` 职责域重排、旅游能力收编为 Sage domain skill、前端 Coding 组件分目录。

## 范围边界

本轮只做 v5.0：

- 不引入 Naive UI、Monaco、xterm、diff drawer。
- 不改现有 REST / WebSocket URL。
- 不删除旅游侧成熟资产：`agents/`、`mcp_servers/`、`core/verifier.py`、`evals/`、`api/routes.py`、`api/ws.py`。
- `App.vue` 继续默认进入 Sage Coding 工作台，旅游能力通过 Skill 和 deferred tools 进入 Sage。

## 后端目录重排

`core/coding` 从平铺 runtime 文件调整为职责域目录：

| v4.1 文件 | v5.0 位置 | 职责 |
| --- | --- | --- |
| `engine.py` | `engine/engine.py` | 模型循环、工具循环、final/step limit orchestration |
| `events.py` | `engine/events.py` | typed RunEvent 契约 |
| `engine_helpers.py` | `engine/helpers.py` | tool payload normalize、工具描述、step limit 摘要 |
| `model_output.py` | `engine/model_output.py` | `<tool>` / `<final>` 输出解析 |
| `tool_executor.py` | `tool_executor/executor.py` | 单个工具执行管线 |
| `approval.py` | `tool_executor/approval.py` | approval 状态与危险命令检测 |
| `permissions.py` | `tool_executor/permissions.py` | write scope / plan mode 权限判断 |
| `tool_policy.py` | `tool_executor/policy.py` | 工具级 policy 判断 |
| `context_manager.py` | `context/manager.py` | system prompt、history、上下文预算 |
| `compact.py` | `context/compact.py` | context compact |
| `workspace.py` | `context/workspace.py` | workspace file/git 视图 |
| `worker_manager.py` | `multiagent/manager.py` | worker 生命周期管理 |
| `worker_execution.py` | `multiagent/execution.py` | worker task 数据结构 |
| `worker_runtime.py` | `multiagent/runtime.py` | worker runtime 组装 |
| `run_store.py` | `persistence/run_store.py` | run trace 持久化 |
| `session_events.py` | `persistence/session_events.py` | session event bus |
| `session_store.py` | `persistence/session_store.py` | coding session 存储 |
| `todo_ledger.py` | `persistence/todo_ledger.py` | todo ledger |

新增包入口：

- `core.coding.engine`
- `core.coding.context`
- `core.coding.tool_executor`
- `core.coding.persistence`
- `core.coding.multiagent`
- `core.coding.memory`

兼容决策：

- `runtime.py` 和 `plan_mode.py` 保持顶层，作为 runtime 组装入口和小型模式模块。
- 不保留旧平铺 shim 文件，避免“目录重排了但实际仍依赖旧文件”的双轨状态。
- `engine/__init__.py` 和 `tool_executor/__init__.py` 对外 re-export 公开接口；内部 runtime 组装使用具体模块路径，避免 lazy re-export 造成类型检查误判。

## 旅游能力收编

新增 Skill：

- `/travel`：`core/coding/skills/bundled/travel/SKILL.md`

保留 Skill：

- `/travel-planning`：继续兼容旧入口，避免破坏已有测试和用户习惯。

新增 deferred travel tools：

- `generate_itinerary`
- `search_attractions`
- `get_weather`
- `get_forecast`
- `geocode`
- `search_nearby`
- `get_route`

实现位置：

- `core/coding/tools/travel_tools.py`
- `core/coding/tools/registry.py` 的 `TOOL_MODULES` 加入 `core.coding.tools.travel_tools`

关键设计：

- 所有旅游工具都是 `category="travel"`、`deferred=True`，普通 coding session 启动时不会强制加载真实外部服务。
- handler 内 lazy 读取 settings / 初始化 client；缺少 API key 时返回明确的 `ToolResult(is_error=True)`，不影响其他工具和普通 coding 工作流。
- `generate_itinerary` 复用已有 `agents/itinerary_tool.py` 与 `agents/graph.py`，测试中通过 monkeypatch fake graph，避免真实 LLM 或外部 API 调用。

## 前端组件分目录

Coding 组件从 `frontend/src/components/Coding*.vue` 迁入：

```text
frontend/src/components/coding/
├── chat/
│   ├── CodingApprovalCard.vue
│   ├── CodingThinkingIndicator.vue
│   └── CodingToolActivity.vue
├── composer/
│   └── CodingComposer.vue
├── files/
│   ├── CodingFileTree.vue
│   └── CodingGitBadge.vue
├── sidebar/
│   └── CodingSidebar.vue
└── index.ts
```

`frontend/src/views/CodingView.vue` 改为从 `components/coding/index.ts` 引入组件。原旅游 `ChatView`、旅游 API、旅游 store 保留，但不作为主入口。

## 测试覆盖

本轮新增/更新的重点测试：

- skill discovery 包含 `/travel`，并继续包含 `/travel-planning`。
- tool registry 能发现 deferred travel tools。
- `tool_search("travel")` 可以激活旅游工具。
- 旅游工具 missing key 返回明确错误，不打真实 API。
- `generate_itinerary` 使用 fake graph 验证接入路径，不打真实 LLM。
- 前端移动后的组件测试继续覆盖 sidebar、approval、thinking、tool activity。

## 阶段验证

已通过：

```bash
pytest tests/core/coding tests/api/test_coding_routes.py -q
# 118 passed, 16 warnings

ruff check core/coding tests/core/coding tests/api/test_coding_routes.py
# All checks passed

mypy core/
# Success: no issues found in 57 source files

cd frontend && npm run test -- --run
# 18 test files passed, 70 tests passed

cd frontend && npm run build
# build passed
```

最终全量验收：

```bash
bash scripts/check.sh
# ruff lint passed
# ruff format --check passed
# mypy passed: 92 source files
# pytest passed: 357 passed, 16 warnings

cd frontend && npm run test -- --run
# 18 test files passed, 70 tests passed

cd frontend && npm run build
# build passed
```

## v5.1 后续建议

v5.1 可以在本轮结构基础上继续做前端体验升级：

- approval UI 的 allow / deny / session scope 交互细化。
- patch / write_file 前后的 diff preview drawer。
- 文件预览迁移到 Monaco，并支持 diff editor。
- 内嵌 terminal 与后端 terminal WebSocket。
- session history / run timeline 的可恢复与可重连体验。
