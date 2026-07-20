# 14 - create_agent 与 LangChain/LangGraph 深入

> Last verified against: `dev/sage-v7@1009e53` (2026-07-20)

> 本章目标：能从源码解释 LangChain 和 LangGraph 的区别、`create_agent` 内部做了什么、StateGraph + checkpointer + middleware 如何协作，以及 Sage 为什么从自研 XML 协议迁移到这套运行时。
>
> 本章讨论框架概念时以 Sage 当前锁定的 LangChain 1.x / LangGraph 1.x 依赖和源码用法为准。

## 1. LangChain vs LangGraph：不是同一个东西

先把两层职责分开，再看它们怎样通过 `create_agent` 组合。

### LangChain：模型 + 工具的标准化抽象

LangChain 解决的是**接口碎片化**问题。每个 LLM provider（OpenAI/Anthropic/Gemini/DeepSeek）的 API 都不一样，每个向量数据库的 SDK 都不一样。LangChain 把它们抽象成统一接口：

```python
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

# 两个 provider，同一个接口
model_openai = ChatOpenAI(model="gpt-4o")
model_anthropic = ChatAnthropic(model="claude-sonnet-4-20250514")

# 调用方式完全一样
response = await model_openai.ainvoke([{"role": "user", "content": "hello"}])
response = await model_anthropic.ainvoke([{"role": "user", "content": "hello"}])
```

LangChain 的核心抽象：

| 抽象 | 作用 |
| --- | --- |
| `BaseChatModel` | 统一的模型接口（`ainvoke` / `astream` / `bind_tools`） |
| `BaseMessage` | 统一的消息类型（HumanMessage/AIMessage/SystemMessage/ToolMessage） |
| `BaseTool` | 统一的工具接口（`_run` / `_arun` + schema） |
| `BaseRetriever` | 统一的检索器接口 |
| `BaseEmbeddings` | 统一的 embedding 接口 |

**LangChain 不是 agent 框架**，它是 LLM 应用的基础设施层。

### LangGraph：有状态的图执行引擎

LangGraph 解决的是**复杂有状态工作流**问题。LangChain 的 Chain 是线性的（A -> B -> C），但 agent 是循环的（model -> tool -> model -> tool -> ...），还有分支、中断、恢复、人工审批。

LangGraph 把工作流建模成**状态图（StateGraph）**：

```python
from langgraph.graph import StateGraph, START, END

graph = StateGraph(MyState)
graph.add_node("model", call_model)
graph.add_node("tools", call_tools)
graph.add_edge(START, "model")
graph.add_conditional_edges("model", should_continue, {"continue": "tools", "end": END})
graph.add_edge("tools", "model")  # 工具执行后回到模型
app = graph.compile()
```

LangGraph 的核心概念：

| 概念 | 作用 |
| --- | --- |
| `StateGraph` | 有状态图，节点之间传递 state |
| `State`（TypedDict） | 图的状态，带 reducer（合并策略） |
| `Node` | 图的节点，接收 state 返回 state 更新 |
| `Edge` / `Conditional Edge` | 边，决定下一个节点 |
| `Checkpointer` | 状态持久化，支持中断/恢复 |
| `Command` | 控制 graph 执行（resume/interrupt/goto） |
| `interrupt()` | 中断 graph，等待外部输入 |
| `Runtime` | 运行时上下文，注入非状态数据 |

**LangGraph 是 agent 框架**，它让你能用图的方式定义复杂的有状态工作流。

### 两者的关系

```
LangChain（基础设施）
  ├── BaseChatModel（模型抽象）
  ├── BaseTool（工具抽象）
  ├── BaseMessage（消息抽象）
  └── ...

LangGraph（执行引擎）
  ├── StateGraph（图）
  ├── Checkpointer（持久化）
  ├── Command/interrupt（控制流）
  └── 依赖 LangChain 的抽象

create_agent（LangChain 1.x 提供）
  ├── 用 LangChain 的 BaseChatModel + BaseTool
  ├── 用 LangGraph 的 StateGraph + Checkpointer
  └── 返回一个 CompiledStateGraph（LangGraph 图）
```

**关键**：`create_agent` 是 LangChain 1.x 提供的工厂函数，它内部用 LangGraph 构建图。返回值是 LangGraph 的 `CompiledStateGraph`。

## 2. `create_agent` 内部做了什么

很多人以为 `create_agent` 是个简单的 ReAct 循环。实际上它构建了一个**可扩展的图 + middleware 链**。

### 最简调用

```python
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

agent = create_agent(
    model=ChatOpenAI(model="gpt-4o"),
    tools=[...],
)
```

这一行背后做了这些事：

```
1. 创建 StateGraph(AgentState)
2. 添加两个节点：
   - "model"：调用 model.bind_tools(tools)
   - "tools"：执行 tool_calls
3. 添加边：
   - START -> "model"
   - "model" -> conditional(有 tool_calls ? "tools" : END)
   - "tools" -> "model"
4. 应用 middleware 链（如果有）
5. 绑定 checkpointer（如果有）
6. compile() 返回 CompiledStateGraph
```

### 图的结构

```
START
  ↓
[model node]
  ↓
  ├── 有 tool_calls? ──Yes──> [tool node] ──> 回到 [model node]
  │
  └── No ──> END
```

这是一个**循环图**，不是线性 chain。`model` 节点调用模型，如果模型返回 `tool_calls`，跳到 `tools` 节点执行工具，然后回到 `model` 节点。直到模型不再返回 `tool_calls`（给出最终回答），跳到 END。

### Sage 的 create_sage_agent

```python
def create_sage_agent(
    model: BaseChatModel,
    tools: Sequence[ToolLike] | None = None,
    *,
    system_prompt: str | None = None,
    config: HarnessConfig | None = None,
    registry: MiddlewareRegistry | None = None,
    middleware: Sequence[Middleware] | None = None,
    state_schema: type[SageThreadState] = SageThreadState,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    name: str = "sage",
) -> CompiledStateGraph[Any, HarnessRunContext, Any, Any]:
    # middleware 和 registry 不能同时传（互斥）
    if middleware is not None and registry is not None:
        raise ValueError("middleware is a full takeover and cannot be combined with registry")

    effective_config = config or HarnessConfig()
    effective_middleware = (
        list(middleware)
        if middleware is not None
        else (registry or build_default_registry()).build(effective_config)
    )

    return create_agent(
        model=model,
        tools=list(tools) if tools else None,
        system_prompt=system_prompt,
        middleware=effective_middleware,
        state_schema=state_schema,           # SageThreadState（扩展 AgentState）
        context_schema=HarnessRunContext,    # 运行时上下文
        checkpointer=checkpointer,           # SQLite checkpointer
        name=name,
    )
```

Sage 的扩展点：

| 参数 | Sage 传什么 | 作用 |
| --- | --- | --- |
| `model` | `ChatOpenAI` / `ChatAnthropic` | 模型客户端 |
| `tools` | 25 个工具（转成 BaseTool） | 模型可调用的工具 |
| `system_prompt` | Sage system prompt | 身份 + 行为准则 |
| `middleware` | 默认 registry 的 8 个 middleware | 横切逻辑（净化/预算/错误处理） |
| `state_schema` | `SageThreadState` | 扩展 AgentState，加 durable channel |
| `context_schema` | `HarnessRunContext` | 运行时身份（owner/workspace/run） |
| `checkpointer` | SQLite checkpointer | 状态持久化 |

## 3. State 和 Reducer：状态怎么合并

LangGraph 的 State 是 `TypedDict`，每个字段可以带 **reducer**（合并策略）。

### 为什么需要 reducer

Graph 的每个节点返回 state 的**部分更新**，不是完整 state。LangGraph 用 reducer 决定怎么把更新合并到当前 state。

```python
# 节点返回
def call_model(state):
    response = model.invoke(state["messages"])
    return {"messages": [response]}  # 只返回新增的消息

# LangGraph 怎么合并？
# 如果用默认策略（覆盖）：state["messages"] = [response]  # 旧消息丢了！
# 如果用 add_messages reducer：state["messages"] = state["messages"] + [response]  # 追加
```

### Sage 的 SageThreadState

```python
from langgraph.graph import add_messages
from langchain.agents import AgentState

class SageThreadState(AgentState):
    # AgentState 已经定义了 messages: Annotated[list, add_messages]

    # Sage 扩展的 durable channel
    thread_data: Annotated[NotRequired[ThreadDataState | None], merge_thread_data]
    sandbox: Annotated[NotRequired[SandboxState | None], merge_sandbox]
    artifacts: Annotated[NotRequired[list[ArtifactRef] | None], merge_artifacts]
    todos: Annotated[NotRequired[list[TodoItem] | None], merge_todos]
    goal: Annotated[NotRequired[GoalState | None], merge_goal]
    delegations: Annotated[NotRequired[list[DelegationEntry] | None], merge_delegations]
    evidence_refs: Annotated[NotRequired[list[str] | None], merge_evidence_refs]
    skill_context: Annotated[NotRequired[list[SkillRef] | None], merge_skill_context]
    memory_refs: Annotated[NotRequired[list[MemoryRef] | None], merge_memory_refs]
    approval_context: Annotated[NotRequired[ApprovalContext | None], merge_approval_context]
    summary_text: Annotated[NotRequired[str | None], merge_summary]  # 独立 channel
```

### reducer 的设计原则

Sage 的 reducer 设计遵循几个原则：

**① 终态不可降级**

```python
def merge_delegations(current, update):
    # 如果 delegation 已经是 terminal 状态，不能降级回 running
    for entry in current:
        if entry.id in [u.id for u in update]:
            if entry.status in TERMINAL_STATUSES:
                # 保留 terminal 状态，忽略 update 的降级
                continue
    return merged
```

**② 稳定 ID 去重**

```python
def merge_artifacts(current, update):
    # artifacts 按 artifact_id 去重，新版本覆盖旧版本
    by_id = {a["id"]: a for a in current}
    for artifact in update:
        by_id[artifact["id"]] = artifact  # 覆盖
    return list(by_id.values())
```

**③ 独立 channel 不伪装成消息**

`summary_text` 是独立 channel，不是 messages 列表里的一条消息。这解决了 V6 时期"压缩摘要混在 history 里，模型分不清哪些是真实对话哪些是摘要"的问题。

### 理解检查

> **Q: LangGraph 的 State 为什么要用 reducer？**
>
> A: 因为 graph 的节点返回的是 state 的部分更新，不是完整 state。reducer 决定怎么合并更新到当前 state。比如 `add_messages` reducer 把新消息追加到列表，而不是覆盖。这让多个节点可以独立更新 state 的不同字段，不用关心其他字段。

## 4. Checkpointer：状态持久化与恢复

### 为什么需要 checkpointer

Agent 是长任务。用户可能：
- 关闭浏览器（WebSocket 断开）
- 服务器重启
- 需要人工审批（中断 graph，等用户决策）

这些场景都要求 graph 的状态能**持久化**和**恢复**。Checkpointer 就是做这个的。

### Sage 的 SQLite checkpointer

```python
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver(conn)
agent = create_sage_agent(
    model=model,
    tools=tools,
    checkpointer=checkpointer,
)
```

每次 graph 执行到某个节点，checkpointer 把当前 state 存到 SQLite。恢复时从 SQLite 读回 state，从中断点继续。

### thread_id 的作用

```python
config = {"configurable": {"thread_id": "session_123"}}

# 第一次调用
await agent.ainvoke({"messages": [HumanMessage("hello")]}, config=config)
# state 保存到 SQLite，key = thread_id="session_123"

# 第二次调用（同一个 thread_id）
await agent.ainvoke({"messages": [HumanMessage("what did I say?")]}, config=config)
# 从 SQLite 恢复 state，模型记得之前说过 "hello"
```

`thread_id` 是 thread 的唯一标识。同一个 `thread_id` 的多次调用共享 state。不同 `thread_id` 的 state 隔离。

### checkpointer vs Sage timeline

这里最容易混淆的是：LangGraph checkpointer 和 Sage 的 SessionEventJournal 都做持久化，为什么不合并？

| 存储 | 职责 | 内容 |
| --- | --- | --- |
| LangGraph checkpointer | Agent 执行状态恢复 | graph 内部 state（messages/artifacts/todos/...） |
| Sage SessionEventJournal | 用户可见事件审计 | 用户可见事件序列（tool_call/tool_result/approval/...） |

**不合并的原因**：
- checkpointer 是 graph 内部状态，包含中间步骤、重试、协议细节，用户不需要看到
- timeline 是用户可见审计，只包含用户关心的事件
- 两者生命周期不同：checkpointer 可以被压缩/清理，timeline 是永久审计记录
- 合并会让 graph 内部状态污染审计

## 5. Middleware：横切逻辑的组合

### 为什么需要 middleware

没有 middleware 时，横切逻辑（净化/预算/错误处理/审计）散落在 graph 节点里。节点变臃肿，逻辑耦合，难以测试。

middleware 把横切逻辑抽成独立的可组合单元：

```python
class InputSanitizationMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    @override
    def wrap_model_call(self, request, handler):
        sanitized = _sanitize_last_user_message(request.messages)
        return handler(request.override(messages=sanitized))
```

### middleware 的 hook 类型

LangChain 1.x 的 `AgentMiddleware` 提供多个 hook：

| Hook | 作用 | Sage 用法 |
| --- | --- | --- |
| `before_agent` | graph 执行前 | `ThreadContextMiddleware` 注入 owner/workspace |
| `after_agent` | graph 执行后 | `TerminalResponseMiddleware` 禁止静默成功 |
| `before_model` | 每次调模型前 | `RunBudgetMiddleware` 检查预算 |
| `after_model` | 每次调模型后 | `TokenBudgetMiddleware` 累加 token |
| `wrap_model_call` | 包裹模型调用 | `InputSanitizationMiddleware` 净化输入 |
| `before_tool` | 工具执行前 | （设计：ApprovalInterrupt） |
| `after_tool` | 工具执行后 | （设计：ToolProgress） |

### `@hook_config(can_jump_to=["end"])`

```python
class RunBudgetMiddleware(AgentMiddleware[...]):
    @hook_config(can_jump_to=["end"])
    @override
    def before_model(self, state, runtime):
        if used_tokens >= self.max_tokens:
            return {"messages": [AIMessage(content="token budget exhausted")]}
            # 因为有 can_jump_to=["end"]，graph 会直接跳到 END
        return None  # 正常继续
```

`@hook_config(can_jump_to=["end"])` 让 middleware 能**直接跳到 END**，跳过后续节点。这是预算耗尽时终止 graph 的机制。

### middleware 的顺序很重要

```python
# sage_harness/middleware/registry.py
def build_default_registry() -> MiddlewareRegistry:
    registry = MiddlewareRegistry()
    # 注册顺序 = 执行顺序
    registry.register(InputSanitizationMiddleware)
    registry.register(ThreadContextMiddleware)
    registry.register(ProviderErrorMiddleware)
    registry.register(ToolErrorMiddleware)
    registry.register(RemoteContentSanitizationMiddleware)
    registry.register(ToolResultArtifactMiddleware)
    registry.register(RunBudgetMiddleware)
    registry.register(TokenBudgetMiddleware)
    registry.register(TerminalResponseMiddleware)
    registry.register(DurableContextMiddleware)
    return registry
```

**为什么顺序重要**：
- `InputSanitizationMiddleware` 必须在 `ThreadContextMiddleware` 之前（先净化输入再注入上下文）
- `RunBudgetMiddleware` 必须在 `before_model`（预算检查在调模型之前）
- `TerminalResponseMiddleware` 必须在 `after_agent`（graph 结束后才检查是否有终态）

顺序错误可能绕过安全检查。Sage 用 `test_middleware_order.py` 锁定顺序。

### 理解检查

> **Q: middleware 和 graph 节点有什么区别？**
>
> A: graph 节点是业务逻辑（调模型/执行工具），middleware 是横切逻辑（净化/预算/审计/错误处理）。middleware 独立于节点，可以组合、替换、重新排序。这让业务逻辑和横切关注点解耦。
>
> **Q: 为什么不把所有逻辑放进 graph 节点？**
>
> A: 会让节点变臃肿、逻辑耦合、难以测试。middleware 让每个横切关注点独立测试，通过组合构建完整行为。

## 6. Context Schema：运行时身份

### State vs Context 的区别

这是 LangGraph 最容易混淆的概念：

| 概念 | 特点 | 例子 |
| --- | --- | --- |
| **State** | 持久化到 checkpointer，跨轮保留 | messages / artifacts / todos |
| **Context** | 不持久化，每次调用传入 | owner_id / run_id / workspace_path |

```python
@dataclass(frozen=True, slots=True)
class HarnessRunContext:
    """Server-owned identity and workspace binding for one graph invocation."""
    thread_id: str
    run_id: str
    owner_id: str
    workspace_id: str
    workspace_path: str
    surface: str = "coding"
    metadata: Mapping[str, object] = field(default_factory=dict)
```

**为什么 run_id 不放 State**：
- run_id 是一次执行的标识，不是持久状态
- 同一个 thread 可以有多次 run（用户发多条消息）
- run_id 每次 invocation 都不同，放 State 会被 checkpointer 持久化，导致旧 run 的 run_id 残留

### middleware 怎么访问 context

```python
class ThreadContextMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    def before_agent(self, state, runtime: Runtime[HarnessRunContext]):
        context = runtime.context  # 从 runtime 拿 context
        if not isinstance(context, HarnessRunContext):
            raise MissingRunContextError("HarnessRunContext is required")
        # 把 context 的值投影到 state（持久化）
        return {
            "thread_data": {
                "owner_id": context.owner_id,
                "workspace_id": context.workspace_id,
                "thread_id": context.thread_id,
            }
        }
```

`runtime.context` 是 `HarnessRunContext`，在 graph 调用时传入。middleware 把 context 的值投影到 state（这样 state 里有 owner_id 等信息，但 context 本身不持久化）。

## 7. Command 和 interrupt：人工审批

### interrupt：暂停 graph

```python
from langgraph.types import interrupt

def risky_tool(state):
    # 工具执行前检查是否需要审批
    if needs_approval(state):
        # 中断 graph，等待外部输入
        decision = interrupt({
            "tool": "run_shell",
            "args": state["pending_tool_args"],
            "reason": "dangerous_command",
        })
        # decision 来自外部恢复
        if decision == "deny":
            return {"messages": [ToolMessage(content="denied")]}
    # 执行工具
    ...
```

`interrupt()` 会：
1. 暂停 graph 执行
2. checkpointer 保存当前 state
3. 返回给调用方一个中断信号
4. 等待外部用 `Command(resume=...)` 恢复

### Command：恢复 graph

```python
from langgraph.types import Command

# 用户批准了审批
result = await agent.ainvoke(
    Command(resume="approve"),  # 恢复 graph，decision = "approve"
    config={"configurable": {"thread_id": "session_123"}},
)
```

`Command(resume=...)` 让 graph 从中断点继续，`interrupt()` 返回的值就是 `resume` 的值。

### Sage 的审批恢复（设计）

```python
# 服务端重建 decision（不信任客户端 payload）
async def handle_approval(approval_id, user_choice):
    approval = approval_store.get(approval_id)
    # 验证 approval 绑定（session_id + run_id + tool_call_id + args_digest）
    if approval.args_digest != current_args_digest:
        raise ValueError("approval args changed")

    # 服务端构造 Command，不是直接把客户端 payload 送进 graph
    result = await agent.ainvoke(
        Command(resume={"decision": user_choice}),
        config={"configurable": {"thread_id": approval.thread_id}},
    )
```

**关键安全设计**：客户端只提交决策（approve/deny），服务端重建 `Command`。不能让客户端直接构造 `Command(resume=...)`，否则客户端可以伪造任意工具结果。

> [!warning] 当前状态
> Sage v7-beta 的 LangGraph durable interrupt 未实现，审批仍用同进程 `ApprovalManager`。这是已知边界。

## 8. Stream：三种流模式

```python
class HarnessRunManager:
    STREAM_MODES: ClassVar[list[str]] = ["values", "messages", "custom"]

    async def stream(self, request: HarnessRunRequest) -> AsyncIterator[HarnessStreamItem]:
        config = thread_config(request.thread_id, ...)
        async for item in self.graph.astream(
            input,
            config=config,
            context=request.context,
            stream_mode=["values", "messages", "custom"],
        ):
            yield normalize_stream_item(item)
```

三种流模式：

| 模式 | 产出 | 用途 |
| --- | --- | --- |
| `values` | 每次节点执行后的完整 state | 追踪 state 变化 |
| `messages` | 模型流式输出的 chunk | 实时显示模型回复（text_delta） |
| `custom` | 节点内 `get_stream_writer()` 发的自定义事件 | 工具进度/审批请求/子代理状态 |

### Sage 怎么用这三种流

```python
# HarnessEventAdapter 把 LangGraph stream 转成 Sage 事件
class HarnessEventAdapter:
    def adapt(self, stream_item) -> SageEvent:
        if stream_item.mode == "messages":
            # messages-tuple -> text_delta
            return TextDeltaEvent(delta=stream_item.chunk.content)
        elif stream_item.mode == "custom":
            # custom event -> tool_call / approval_required / subagent_progress
            return self._adapt_custom(stream_item.data)
        elif stream_item.mode == "values":
            # values -> state snapshot（用于 context_usage_updated）
            return self._adapt_values(stream_item.values)
```

**关键**：LangGraph 的流是 graph 内部事件，Sage 的 timeline 是用户可见事件。`HarnessEventAdapter` 做转换--过滤掉用户不关心的内部事件，把用户关心的转成 Sage 事件契约。

## 9. 为什么选 LangGraph 而不是自研

### V6 的自研 XML 协议

V6 的 Sage 用自研 XML 协议：

```xml
<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>
<final>已修正 README.md 中的错字。</final>
```

优点：
- 不依赖 provider 的 function calling 支持
- 任何能输出文本的模型都能用

缺点：
- 模型输出格式不规范时容易进 retry
- 流式输出需要手动过滤 `<tool>` JSON
- 横切逻辑散落在 Engine 里，没有组合边界
- 没有 checkpoint，断线就丢
- 没有 middleware，加新功能要改 Engine

### V7 的 LangGraph create_agent

优点：
- 原生 tool calling，协议稳定
- checkpointer 天然支持中断/恢复
- middleware 链让横切逻辑可组合
- State + reducer 让状态合并有明确语义
- stream 三模式覆盖实时/审计/自定义

缺点：
- 依赖 LangChain/LangGraph 版本（升级有破坏性变更风险）
- 要求 provider 支持结构化 tool calling
- 学习曲线陡（State/reducer/context/middleware 概念多）

### Sage 的选择：双轨

```
legacy       自研 XML Engine（V6，历史兼容）
deerflow_v2  LangGraph create_agent（V7，未来方向）
```

不直接改写旧 session。当前新会话已默认使用 v2，legacy 继续解释历史 session 并承担
迁移回归。

## 10. 完整的执行流程

把所有概念串起来，一次 Sage v2 run 的完整流程：

```
1. 用户发消息
   ↓
2. api/coding.py 创建 HarnessRunRequest
   ├── thread_id: session_id
   ├── run_id: run_xxx
   ├── context: HarnessRunContext(owner_id, workspace_id, ...)
   └── message: 用户消息
   ↓
3. HarnessRunManager.stream(request)
   ├── 构造 config = {"configurable": {"thread_id": ...}}
   ├── 构造 input = {"messages": [HumanMessage(message)]}
   └── graph.astream(input, config, context, stream_mode)
   ↓
4. LangGraph 执行 graph
   ├── checkpointer 加载 thread state（如果有）
   ├── before_agent hook: middleware 链执行
   │   ├── InputSanitizationMiddleware 净化输入
   │   ├── ThreadContextMiddleware 注入 owner/workspace
   │   └── RunBudgetMiddleware 检查预算
   ├── 进入 model 节点
   │   ├── before_model hook: middleware 链执行
   │   ├── wrap_model_call: InputSanitization 净化 + ProviderError 包裹
   │   ├── model.astream(messages) 流式输出
   │   │   └── stream_mode="messages" 产出 text_delta
   │   ├── after_model hook: TokenBudget 累加 token
   │   └── 模型返回 tool_calls 或 final answer
   ├── 如果有 tool_calls:
   │   ├── 进入 tools 节点
   │   ├── 执行工具（ToolExecutor 五道门）
   │   ├── ToolMessage 写入 state（add_messages reducer 追加）
   │   └── 回到 model 节点
   ├── 如果没有 tool_calls:
   │   └── 进入 END
   ├── after_agent hook: TerminalResponseMiddleware 检查终态
   └── checkpointer 保存最终 state
   ↓
5. HarnessEventAdapter 转换 stream
   ├── messages-tuple -> text_delta
   ├── custom event -> tool_call / approval / subagent_progress
   └── values -> context_usage_updated
   ↓
6. SessionEventJournal 持久化事件（persist-then-push）
   ↓
7. WebSocket 推送给前端
   ↓
8. 前端 applyCodingEvent reducer 更新 UI
```

## 11. 复盘问题

### Q1: LangChain 和 LangGraph 的区别？

A: LangChain 是 LLM 应用的基础设施层，提供模型/工具/消息的统一抽象。LangGraph 是有状态图执行引擎，用 StateGraph 建模复杂工作流，支持循环/分支/中断/恢复。`create_agent` 是 LangChain 1.x 提供的工厂函数，内部用 LangGraph 构建图。

### Q2: 为什么用 StateGraph 而不是线性 Chain？

A: Agent 是循环的（model -> tool -> model -> ...），不是线性的。线性 Chain 无法表达循环和条件分支。StateGraph 用节点和边建模，天然支持循环图。而且 StateGraph 有 checkpointer，支持中断/恢复。

### Q3: State 的 reducer 是什么？为什么需要？

A: reducer 是 state 字段的合并策略。Graph 节点返回 state 的部分更新，reducer 决定怎么合并到当前 state。比如 `add_messages` reducer 把新消息追加到列表，而不是覆盖。这让多个节点可以独立更新 state 的不同字段。

### Q4: checkpointer 和 event log 有什么区别？

A: checkpointer 是 graph 内部执行状态，用于中断/恢复（包含中间步骤、重试、协议细节）。event log 是用户可见审计，用于 replay/取证（只包含用户关心的事件）。两者职责不同，不合并。合并会让 graph 内部状态污染审计。

### Q5: middleware 和 graph 节点有什么区别？

A: 节点是业务逻辑（调模型/执行工具），middleware 是横切逻辑（净化/预算/审计/错误处理）。middleware 独立于节点，可以组合、替换、重新排序。这让业务逻辑和横切关注点解耦，每个 middleware 独立测试。

### Q6: 为什么 State 和 Context 要分开？

A: State 持久化到 checkpointer，跨轮保留（messages/artifacts/todos）。Context 不持久化，每次调用传入（owner_id/run_id/workspace_path）。run_id 每次 invocation 都不同，放 State 会被持久化导致旧 run 的 run_id 残留。

### Q7: interrupt 和 Command 怎么做人工审批？

A: 工具执行前调用 `interrupt()`，graph 暂停，checkpointer 保存 state。用户在前端审批后，服务端用 `Command(resume=decision)` 恢复 graph，`interrupt()` 返回 decision。关键安全设计：客户端只提交决策，服务端重建 Command，不让客户端直接构造 resume payload。

### Q8: 为什么用 create_agent 而不是手写 StateGraph？

A: `create_agent` 内部已经构建了标准的 model-tool 循环图，加了 tool calling 解析、错误处理、stream 支持。手写 StateGraph 要重新实现这些。`create_agent` 还支持 middleware 链，让横切逻辑可组合。只在需要非标准图结构（如多 agent 协作、自定义节点）时才手写 StateGraph。

### Q9: Sage 为什么双轨（legacy + deerflow_v2）？

A: 旧 session 的 history/trace/diff 使用 legacy 协议，直接改写会改变历史 replay 语义。
新会话已经默认使用 v2；双轨继续让旧 session 按原 profile 解释，并保留迁移回归路径。

### Q10: stream 的三种模式分别用于什么？

A: `values` 产出每次节点执行后的完整 state，用于追踪 state 变化；`messages` 产出模型流式输出的 chunk，用于实时显示模型回复；`custom` 产出节点内自定义事件，用于工具进度/审批请求/子代理状态。Sage 用 `HarnessEventAdapter` 把这三种流转成 Sage 的事件契约。

## 12. 第一入口

按顺序打开：

1. `packages/sage_harness/sage_harness/agents/factory.py::create_sage_agent` - 工厂函数
2. `packages/sage_harness/sage_harness/state.py::SageThreadState` - State + reducer
3. `packages/sage_harness/sage_harness/config.py::HarnessRunContext` - Context
4. `packages/sage_harness/sage_harness/middleware/registry.py` - 默认 8 个 middleware 及顺序
5. `packages/sage_harness/sage_harness/middleware/registry.py::build_default_registry` - 顺序
6. `packages/sage_harness/sage_harness/runtime/manager.py::HarnessRunManager` - 执行管理
7. `core/harness/runtime_adapter.py::SageHarnessRuntimeAdapter` - Sage 适配
8. `core/harness/event_adapter.py::HarnessEventAdapter` - 事件转换

## 测试证据

- `tests/harness/test_agent_factory.py` - 工厂函数
- `tests/harness/test_thread_state.py` - State + reducer
- `tests/harness/test_middleware_order.py` - middleware 顺序
- `tests/harness/test_runtime_manager.py` - 执行管理
- `tests/core/harness/test_harness_runtime_adapter.py` - Sage runtime 与事件适配

## 当前边界

> [!warning] create_agent 相关的已知边界
> - 默认 registry 当前包含 8 个最小安全 middleware；其他能力分布在 adapter/runtime
> - LangGraph durable interrupt 未实现（审批用同进程 ApprovalManager 兜底）
> - `deerflow_v2` 已是新会话默认 profile；legacy 仍用于历史 session
> - checkpointer 用 SQLite，多实例部署需要换 PostgreSQL checkpointer
> - middleware 的 `before_tool` / `after_tool` hook 尚未使用（ApprovalInterrupt/ToolProgress 未实现）

## 自测

1. LangChain 和 LangGraph 的区别？`create_agent` 属于哪个？
2. StateGraph 为什么比线性 Chain 更适合 agent？
3. State 的 reducer 是什么？为什么 `messages` 用 `add_messages` 而不是覆盖？
4. checkpointer 和 event log 为什么不合并？
5. middleware 和 graph 节点的区别？为什么顺序很重要？
6. State 和 Context 为什么要分开？run_id 为什么放 Context 不放 State？
7. `interrupt()` 和 `Command(resume=...)` 怎么做人工审批？为什么客户端不能直接构造 resume payload？
8. stream 的三种模式（values/messages/custom）分别用于什么？
9. Sage 为什么双轨（legacy + deerflow_v2）？为什么不直接替换？
10. `@hook_config(can_jump_to=["end"])` 的作用？
