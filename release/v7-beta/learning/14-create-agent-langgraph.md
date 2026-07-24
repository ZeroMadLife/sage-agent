# create_agent 与 LangGraph：Sage 用框架组图，不把产品边界交给框架

Sage 使用框架的主线可以先压成一句话：`create_agent` 负责把模型、工具、状态和 middleware 编译成图，Sage 仍然负责身份、权限、持久化、事件和产品终态。

> Last verified against: `dev/sage-v7@236e07c` (2026-07-23)

![create_agent 与 LangGraph](assets/14-create-agent-langgraph.png)

三层关系可以这样看：

```text
LangChain：模型、消息、工具和 middleware 接口
LangGraph：状态图、reducer、checkpoint、interrupt 与 resume
Sage：运行上下文、能力装配、事实存储、事件协议和安全边界
```

Sage 不是重新实现 LangGraph，也不是把整个产品交给 `create_agent`。它在框架抽象外面保留了一层明确的 harness。

## `create_agent` 解决组图，不解决产品语义

LangChain 1.x 的 `create_agent` 接收模型、工具、system prompt、middleware、state schema、context schema 和 checkpointer，返回一个可执行的 `CompiledStateGraph`。

它提供的核心循环是：

```text
START
  -> model
  -> 有 tool_calls 时进入 tools
  -> 工具结果回到 model
  -> 没有后续工具时进入 END
```

这比手写 `while` 循环多了状态合并、stream、checkpoint、中断恢复和 middleware hook，但它仍然不知道：

- 当前 run 属于哪个 owner 和 workspace；
- 哪些工具在这个 surface 可用；
- 什么动作必须审批或进入 container；
- 哪些事件需要写进用户可见 timeline；
- graph END 是否已经形成 Sage 的 `run_finished`；
- Knowledge、Memory 和 Artifact 应该写到哪里。

这些语义由 Sage 的应用层补齐。

## `create_sage_agent` 是纯工厂，不是隐藏 Runtime

`packages/sage_harness/sage_harness/agents/factory.py::create_sage_agent` 是 Sage 对 `create_agent` 的窄封装。

它只做四件事：

1. 接收已经构造好的 model 和 tools；
2. 根据 `HarnessConfig` 构造有序 middleware；
3. 指定 `SageThreadState` 与 `HarnessRunContext`；
4. 将可选 checkpointer 传给框架并返回 compiled graph。

它不会读 `.env`、打开数据库、发现 workspace、选择用户模型或创建 KnowledgeStore。这样 package 可以独立测试，Sage 产品也不会被工厂中的全局状态锁死。

自定义 `middleware` 与 `registry` 互斥。前者意味着完全接管，后者是在稳定默认链上按命名 anchor 扩展。两者同时传入会直接失败，避免出现两条相互覆盖的装配规则。

## State 和 Context 不是同一类数据

这是读 LangGraph 集成时最重要的边界。

| 对象 | 保存什么 | 是否进入 checkpoint |
| --- | --- | --- |
| `SageThreadState` | messages、todo、goal、delegation、artifact ref、memory ref、approval context | 是 |
| `HarnessRunContext` | owner、workspace、thread、run、workspace path、surface | 否，由服务器每次调用注入 |

State 表示可以随图执行演进并恢复的状态。Context 表示本次调用由服务器确认的身份与资源绑定。

如果把 owner 或 workspace 只放进模型消息，模型可以伪造；如果把数据库连接和大对象放进 state，checkpoint 会膨胀并失去可移植性。

`ThreadContextMiddleware` 会把必要的 server-owned 标识投影为 checkpoint-safe reference，但真正的 `HarnessRunContext` 仍由调用端提供。

## Reducer 是状态契约，不是列表拼接工具

LangGraph 节点返回局部更新，reducer 决定新旧状态怎样合并。Sage 的 reducer 保护几个关键不变量：

- thread、owner、workspace 和 sandbox identity 冲突时失败；
- artifact、memory、skill 和 evidence 依据稳定 ID 去重并限制数量；
- delegation、goal 和 approval 的终态不能被旧事件降级；
- budget 使用量不能因为恢复旧 checkpoint 而变小；
- summary、todo 和 promoted tools 各自拥有明确的覆盖或清空语义。

因此 reducer 是持久状态的并发与恢复规则。随意把 `append` 改成 `replace`，可能不会立刻报错，却会在 resume、并行子任务或重放时丢状态。

## 默认 middleware 顺序是协议

当前默认 registry 固定为八个 middleware：

```text
input_sanitization
thread_context
durable_context
provider_error
remote_content_sanitization
tool_error
run_budget
terminal_response
```

这不是装饰性插件列表，而是一条执行协议。

输入先被标记为不可信数据，server-owned context 再被投影，durable context 才进入模型视野；provider 与 remote tool error 在各自边界归一化；预算限制循环；最后 terminal middleware 防止 graph 在没有用户可见回答时静默成功。

扩展 middleware 必须相对命名 anchor 插入，并由顺序测试固定。仅仅“八个对象都存在”不能证明行为相同。

## HarnessRunManager 负责调用图，不拥有 Sage run lease

`HarnessRunManager` 把 `HarnessRunRequest` 转成 LangGraph 的调用：

- 生成 thread config 与 recursion limit；
- 对新消息使用 `HumanMessage`；
- 对 interrupt resume 使用 `Command(resume=...)`；
- 请求 `values`、`messages` 和 `custom` stream；
- 归一化框架 stream item；
- 处理 timeout 与 recursion error。

它不创建 Sage session，也不决定 run 能否并发。run lease、timeline、transcript、diff 和最终证据仍由 Sage 应用 Runtime 管理。

这个分工让通用 package 可以运行一张图，同时避免它偷偷成为第二套产品控制面。

## Runtime Adapter 把图事件翻译成 Sage 事件

`core/harness/runtime_adapter.py::SageHarnessRuntimeAdapter` 是框架与产品之间的桥。

它负责：

- 从 Sage model、tool、capability 和 port 构造 graph；
- 将 workspace、owner、run 和 surface 绑定到 context；
- 把 LangGraph stream 转成 Sage typed events；
- 投影 approval interrupt，而不泄漏 checkpoint 内容；
- 连接 artifact、memory、knowledge、subagent、MCP 和 sandbox adapter；
- 将终态交回应用 Runtime 做持久化收口。

因此调试 v2 时要区分三种问题：graph 是否正确推进、adapter 是否正确翻译、Sage Runtime 是否正确持久化。把三者混成“LangGraph 出错”会失去定位能力。

## Checkpointer 不是 Timeline，也不是 Transcript

LangGraph checkpointer 保存图执行状态，用于同一 thread 的继续、interrupt 和 resume。

Sage 还需要另外几种事实：

| 存储 | 解决的问题 |
| --- | --- |
| Checkpointer | 图从哪个状态继续 |
| TranscriptStore | 模型对话与工具消息的 canonical history |
| SessionEventJournal | 用户看见的事件怎样重放 |
| RunStore | 这次 run 为什么结束、产生什么 diff 和 trace |
| ArtifactStore | 大结果怎样脱离 prompt 保存并引用 |

只保存 checkpoint，前端无法得到稳定的用户可见 timeline；只保存 timeline，graph interrupt 又无法恢复内部 state。它们通过稳定 ID 对齐，不应合并成一个万能表。

## 为什么不继续维护手写 XML 循环

legacy Engine 的价值是协议透明、容易调试，也保存了历史 session 的兼容性。但继续向它叠加所有能力会重复建设：

- 原生 provider tool calling；
- state reducer 与并行更新；
- checkpoint、interrupt 和 resume；
- middleware hook 与流式事件；
- 子图和受限子 Agent 生命周期。

迁移到 `create_agent` 的判断不是“框架一定更高级”，而是这些通用机制已经有成熟实现，Sage 应把工程精力放在自己的产品边界、证据和学习闭环上。

legacy 仍然保留，因为旧 transcript 和 XML 工具协议不能被静默重解释。新会话默认 v2，兼容与演进通过 profile 显式分开。

## 为什么也不能直接使用框架默认值

框架默认 graph 可以证明模型会调用工具，但不能自动得到 Sage 的安全和恢复语义。

Sage 仍要显式提供：

- server-owned `HarnessRunContext`；
- 自定义 `SageThreadState` 与 reducer；
- 固定 middleware 顺序；
- capability 与 deferred tool 策略；
- app-owned sandbox、memory、knowledge 和 event ports；
- SQLite checkpointer 生命周期；
- run lease、timeline、diff 和 terminal evidence。

框架减少的是通用图执行复杂度，不是产品责任。

## 和 Claude Code / CodeBuddy 的对标

| 维度 | Sage | 对标系统 |
| --- | --- | --- |
| Agent 循环 | `create_agent` + LangGraph，保留 legacy | Claude Code 有自有 query engine、SDK stream 与完整工具协议 |
| 扩展机制 | 命名 middleware、capability、ports | Claude Code 的 hooks、plugins、MCP 和产品控制面更成熟 |
| 状态恢复 | checkpointer + Sage 多 store | 成熟系统同时维护 transcript、task state 与 telemetry |
| 研发约束 | 纯工厂、显式 context、contract tests | CodeBuddy 强调代码即配置和统一环境 |
| 可读性 | 框架层与产品层边界可直接审计 | 商业系统覆盖更广，内部细节不一定公开 |

Sage 的目标不是复刻 Claude Code 的内部引擎，而是用公开框架获得稳定图执行，再把自己的学习、知识和证据语义做深。

## 最危险的不是框架报错，而是边界悄悄漂移

- 把 owner/workspace 放进模型可写 state，身份可以被状态更新污染；
- middleware 顺序变化，远程内容在净化前进入模型；
- reducer 允许 terminal delegation 回到 running；
- adapter 把 checkpoint 内部对象完整写进 timeline；
- graph 到 END 后直接返回，漏写 Sage `run_finished` 和 diff；
- checkpointer 和 transcript 对同一 thread 的消息范围产生分叉；
- container adapter 失败后退回 host tool；
- package 反向导入 Sage 应用 store，失去独立性。

这些都是 integration contract 失败，不是多写一次 retry 能解决的问题。

## 设计文档级补充：框架抽象的验收边界

设计可以压成四句话：

- `create_agent` 是 graph factory，不是产品 Runtime。
- `SageThreadState` 是可恢复状态，不是任意对象容器。
- `HarnessRunContext` 是服务器权威，不是模型输入。
- Adapter 翻译框架事件，但 canonical facts 仍由 Sage stores 拥有。

### 源码第一入口

按这个顺序读：

1. `packages/sage_harness/sage_harness/agents/factory.py::create_sage_agent`
2. `packages/sage_harness/sage_harness/config.py::HarnessRunContext`
3. `packages/sage_harness/sage_harness/state.py::SageThreadState`
4. `packages/sage_harness/sage_harness/middleware/registry.py::build_default_registry`
5. `packages/sage_harness/sage_harness/runtime/manager.py::HarnessRunManager`
6. `core/harness/runtime_adapter.py::SageHarnessRuntimeAdapter`

### 最小验收清单

| 验收点 | 最低证据 |
| --- | --- |
| 工厂不读取应用全局状态 | `test_agent_factory.py` 与 package boundary test |
| 默认 middleware 顺序稳定 | `test_middleware_order.py` |
| reducer 不破坏终态和身份 | `test_thread_state.py` |
| interrupt 可以 checkpoint 后恢复 | `test_runtime_manager.py` |
| adapter 正确投影 stream 与 approval | `test_harness_runtime_adapter.py` |
| v2 与 legacy 共享关键安全合同 | profile parity tests |
| checkpointer 不替代 timeline | reconnect 与 journal tests |
| graph 结束形成产品终态 | runtime lifecycle 与 terminal response tests |

面试里可以这样收束：Sage 没有把 `create_agent` 当魔法黑盒。LangChain 提供模型和工具抽象，LangGraph 提供状态图与恢复，Sage 在外面保留身份、能力、持久化和证据边界。框架负责组图，产品负责定义什么叫一次可信、可恢复的完成。
