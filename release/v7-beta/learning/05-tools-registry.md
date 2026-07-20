# 05 - Tool Registry 与工具系统

> Last verified against: `dev/sage-v7@1009e53` (2026-07-20)

> 本章目标：能讲清 `@register_tool` 装饰器做了什么、工具的 deferred loading 怎么省 token、`tool_search` 怎么按需提升工具 schema，以及 Pydantic schema 验证的三层防护。

## 工具是模型的"手脚"

模型只能输出文本（legacy XML 或 v2 原生 tool calling），真正读写文件、执行命令、检索知识的是工具。Sage 的工具系统要回答几个问题：

1. 工具怎么注册（新增工具需要改几处）
2. 工具 schema 怎么暴露给模型（全部暴露会浪费 token）
3. 工具参数怎么验证（防止模型乱传参）
4. 工具执行怎么限制（权限/审批/policy）

## `@register_tool` 装饰器

新增一个工具只需要在函数上加装饰器：

```python
@register_tool(
    name="read_file",
    description="Read a file from the workspace.",
    schema={"path": "str"},
    schema_model=ReadFileArgs,
    risky=False,
    category="file",
    deferred=False,
)
def read_file(workspace: WorkspaceContext, args: dict[str, Any], tool_context: ToolContext | None = None) -> ToolResult:
    path = workspace.path(args["path"])
    content = path.read_text(encoding="utf-8", errors="replace")
    workspace.mark_read(path)
    return ToolResult(content=clip(content))
```

装饰器把函数包成 `ToolDefinition`（元数据）+ `RegisteredTool`（可执行）。`ToolDefinition` 携带：

```python
@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    schema: dict[str, str]              # 简化 schema（给模型看）
    schema_model: type[BaseModel]       # Pydantic 模型（验证参数）
    risky: bool                         # 是否需要审批
    category: str                       # 工具类别（file/shell/agent/memory/...）
    timeout: float = 30.0
    deferred: bool = False              # 是否延迟加载
```

Sage 当前有 **25 个注册工具**：

| 类别 | 工具数 | 举例 |
| --- | --- | --- |
| file | 5 | read_file / write_file / patch_file / list_files / search |
| shell | 1 | run_shell |
| todo | 3 | todo_add / todo_update / todo_list |
| plan | 2 | enter_plan_mode / exit_plan_mode |
| agent | 3 | agent / send_message / task_stop |
| memory | 2 | remember / dream |
| knowledge | 2 | knowledge_search / knowledge_learn |
| travel | 7 | generate_itinerary / search_attractions / get_weather / ... |

## Deferred Tool 与 `tool_search`

### 为什么要 deferred

如果 25 个工具的完整 schema 都暴露给模型，每次 prompt 会多出几千 token 的工具描述。但大部分工具在单次对话里用不到（如 `travel` 类工具在 coding 任务里没用）。

Sage 的做法是 **deferred loading**：

```python
@register_tool(
    name="search_attractions",
    description="搜索指定城市的景点 POI。",
    ...,
    deferred=True,  # 不在启动时加载完整 schema
)
def search_attractions(...): ...
```

`deferred=True` 的工具：
- 启动时不进入 active tool 列表
- 模型只看到工具名 + 一行描述（deferred tools 列表）
- 模型调用 `tool_search(query)` 按需激活
- 激活后加入 `activated_tools`（持久化到 session，跨轮有效）

### `tool_search` 的工作流程

```
模型看到 prompt 里的 deferred tools 列表：
  "Deferred tools (use tool_search to activate):
   search_attractions, get_weather, get_forecast, geocode, ..."

模型判断需要天气信息：
  <tool>{"name":"tool_search","args":{"query":"weather"}}</tool>

tool_search 返回：
  "Activated: get_weather, get_forecast.
   get_weather: 查询城市当前天气。
   get_forecast: 查询城市未来天气预报。"

下一轮 prompt 里这两个工具的完整 schema 出现，模型可以直接调用：
  <tool>{"name":"get_weather","args":{"city":"杭州"}}</tool>
```

`activated_tools` 持久化到 session JSON，session resume 后激活状态不丢。

### 为什么不全部暴露

全部暴露的代价：
- 25 个工具的完整 schema ≈ 3000-5000 token
- 每轮 prompt 都带这些 schema，多轮对话累计成本高
- 模型注意力分散，可能调用无关工具

deferred 的代价：
- 多一次 `tool_search` 调用（消耗一轮）
- 模型需要判断"该不该搜索工具"

权衡：**常用工具（file/shell/todo）不 deferred，领域工具（travel/knowledge/memory）deferred**。

## Pydantic 三层参数验证

工具参数验证有三层防护：

### 第 1 层：Pydantic schema 验证

每个工具有 `schema_model`（Pydantic BaseModel）：

```python
class ReadFileArgs(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("path must not be empty")
        return value
```

`validate_tool_arguments(name, args)` 用 Pydantic 验证参数类型和约束。类型不对/必填缺失/值非法直接报错。

### 第 2 层：workspace 条件检查

`validate_tool()` 做 workspace 相关的语义检查：

```python
def validate_tool(name, args, workspace, activated_tools):
    if name == "patch_file":
        old_text = args.get("old_text", "")
        # old_text 必须在文件里唯一出现（否则 patch 歧义）
        content = workspace.path(args["path"]).read_text()
        count = content.count(old_text)
        if count == 0:
            raise ToolArgumentValidationError("old_text not found")
        if count > 1:
            raise ToolArgumentValidationError("old_text not unique")
```

### 第 3 层：执行前 Permission + Policy

```python
# ToolExecutor.execute()
permission = self.permission_checker.check(tool, args, workspace)
if not permission.allowed:
    yield ToolResultEvent(content=permission.reason, is_error=True); return

policy = self.policy_checker.check(tool, args)
if not policy.allowed:
    yield ToolResultEvent(content=policy.message, is_error=True); return
```

这三层分开是因为它们检查的东西不同：
- Pydantic：参数类型和格式（`path` 是不是字符串）
- workspace 条件：参数语义（`old_text` 在文件里唯一吗）
- Permission/Policy：执行权限和做法（plan mode 禁止写、没先读就改文件拒绝）

详见 [权限、审批与沙盒](06-permissions-approval-sandbox.md)。

## RegisteredTool.execute 的线程池 + 超时

```python
@dataclass(frozen=True)
class RegisteredTool:
    name: str
    schema: dict[str, Any]
    description: str
    risky: bool
    runner: ToolRunner
    category: str = "general"
    requires_approval: bool | None = None
    timeout: float = 30.0
    deferred: bool = False

    def execute(self, args: dict[str, Any] | None = None) -> ToolResult:
        future = _TOOL_EXECUTOR.submit(self.runner, args or {})  # ThreadPoolExecutor
        try:
            result = future.result(timeout=self.timeout)
        except TimeoutError:
            return ToolResult(content=f"tool timed out after {self.timeout:g}s", is_error=True)
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)
        if isinstance(result, ToolResult):
            return result
        return ToolResult(content=str(result))
```

关键设计：

- **全局 ThreadPoolExecutor**（`max_workers=16`）：工具执行不阻塞 async loop。
- **超时控制**：默认 30s，travel 工具 90s。超时返回 `is_error=True`。
- **异常归一**：任何异常都转成 `ToolResult(is_error=True)`，不让普通错误炸掉整个 WebSocket。

**已知局限**：超时只是放弃等待 future，**不真正 kill 底层操作**。如果一个 `run_shell` 启动了 `python -m http.server`，30s 超时后 future 返回但 server 进程还在跑。这是 ThreadPoolExecutor 的固有限制。

## ToolContext：工具访问运行时

工具函数签名：

```python
def remember(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,  # 可选
) -> ToolResult:
    ...
```

`ToolContext` 是工具访问运行时的有限窗口：

```python
@dataclass
class ToolContext:
    runtime: Any | None = None        # CodingRuntime（访问 memory_manager/plan_mode/...）
    todo_ledger: Any | None = None
    worker_manager: Any | None = None
```

工具不能直接 import runtime，必须通过 `tool_context`。这是依赖方向控制--工具不知道 runtime 的具体类型，只通过 protocol 访问。

## v2 的 Tool Registry

v2 runtime 把工具统一为 LangChain `BaseTool`：

```python
# core/harness/tools_adapter.py
class SageToolAdapter:
    def to_base_tool(self, registered_tool: RegisteredTool) -> BaseTool:
        # 把 sage的 RegisteredTool 转成 LangChain BaseTool
        ...
```

v2 的工具 metadata 扩展：

```text
risk_level
permission_scope
surface_capabilities
deferred
remote_content
output_policy
artifact_policy
idempotency
```

这些 metadata 让 middleware 能做更精细的策略（如 `RemoteContentSanitizationMiddleware` 处理 `remote_content=True` 的工具结果）。

## 工具的失败归一

工具失败有好几种，必须归一成模型可消费的 `ToolResult`：

| 失败类型 | 例子 | 归一方式 |
| --- | --- | --- |
| 参数验证失败 | `path` 为空 | `ToolResult(is_error=True, content="path must not be empty")` |
| workspace 条件失败 | `old_text` 不唯一 | `ToolResult(is_error=True, content="old_text not unique")` |
| 权限拒绝 | plan mode 写文件 | `ToolResult(is_error=True, content="plan_mode_tool_not_allowed")` |
| 策略拒绝 | 没先读就改 | `ToolResult(is_error=True, content="prior_read_required")` |
| 审批拒绝 | 用户拒绝危险命令 | `ToolResult(is_error=True, content="approval denied")` |
| 执行超时 | 30s 没返回 | `ToolResult(is_error=True, content="tool timed out")` |
| 执行异常 | 文件不存在 | `ToolResult(is_error=True, content=str(exc))` |

**关键**：失败不抛异常，转成 `ToolResult`。模型看到 error 信息后可以自己修正参数重试。这是 agent loop 能"从错误中恢复"的基础。

## 外部参考的使用边界

工具数量和注册语法不能直接代表 Agent 能力。Sage 的关键判断是 schema 是否可验证、工具
是否按需暴露、执行是否经过 policy，以及失败是否归一为稳定事件。外部工具系统需要在
明确版本下单独复核，本章不保留会快速失真的数量比较。

## 第一入口

按顺序打开：

1. `core/coding/tools/registry.py::register_tool` - 装饰器
2. `core/coding/tools/registry.py::build_tool_registry` - 构建 tool registry
3. `core/coding/tools/registry.py::validate_tool_arguments` - Pydantic 验证
4. `core/coding/tools/base.py::RegisteredTool.execute` - 线程池 + 超时
5. `core/coding/tools/file_tools.py::read_file` - 典型工具实现
6. `core/coding/tools/registry.py::_tool_search_definition` - tool_search 实现
7. `core/harness/tools_adapter.py` - v2 工具适配

## 测试证据

- `tests/core/coding/test_tools.py` - 工具注册 + schema 验证
- `tests/core/coding/test_tool_executor.py` - 执行管线
- `tests/core/coding/test_engine.py::test_engine_tool_search_activates_deferred_tools_for_next_prompt` - deferred 激活
- `tests/core/coding/test_tools.py::test_validate_tool_rejects_non_unique_old_text` - workspace 条件

## 当前边界

> [!warning] Tool 系统有几个已知局限
> - 超时不 kill 底层操作（ThreadPoolExecutor 固有限制）
> - `allowed_tools` 在 SKILL.md frontmatter 里解析了但没强制执行（详见 [Skills 与命令系统](07-skills-commands.md)）
> - v2 的 tool metadata（risk_level/permission_scope/...）设计完成，部分实现
> - AST 自动工具发现未实现，当前仍使用显式 `TOOL_MODULES`

## 自测

1. `@register_tool` 装饰器做了什么？新增一个工具需要改几处？
2. Deferred tool 解决什么问题？代价是什么？
3. `tool_search` 激活的工具为什么持久化到 session？
4. Pydantic 三层参数验证各自检查什么？为什么分开？
5. 工具失败为什么不抛异常而转成 `ToolResult(is_error=True)`？
6. 超时为什么不真正 kill 底层操作？这个局限会带来什么风险？

下一章：[权限、审批与沙盒](06-permissions-approval-sandbox.md)
