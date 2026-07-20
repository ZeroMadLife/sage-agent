# 03 - Runtime 与 Engine

> Last verified against: `dev/sage-v7@1009e53` (2026-07-20)

> 本章目标：能用伪代码复述 `Engine.run_turn()` 和 `CodingRuntime.run_turn()`，解释 legacy XML 协议与 `create_agent` 原生 tool calling 的差别，并指出 lease、diff、trace、run_finished 为什么必须在 Runtime 而不是 Engine。

## 两层 Runtime 的职责拆分

Sage 的 runtime 分两层：

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 外层（会话级） | `core/coding/runtime.py::CodingRuntime` | 持有 workspace/session/memory/tools/approval/plan_mode/worker_manager，建立 run 外层生命周期 |
| 内层（turn 级） | `core/coding/engine/engine.py::Engine` | 推进 model -> parse -> tool -> model 循环 |
| v2 外层 | `core/harness/runtime_adapter.py::SageHarnessRuntimeAdapter` | 把 Sage 业务事实接到 sage_harness Port |
| v2 内层 | `packages/sage_harness/sage_harness/agents/factory.py::create_sage_agent` | LangGraph `create_agent` + Middleware 链 |

**关键**：Runtime 管"会话级生命周期"（lease、diff、trace、终态事件、finally 清理），Engine 管"单轮 ReAct 循环"（model -> parse -> tool -> model）。Engine 不知道 session、diff、WebSocket 的存在。

## CodingRuntime.run_turn 的伪代码

```python
async def run_turn(self, user_message):
    # 1. Active-run lease: 拒绝并发
    if self.active_run_id is not None:
        yield ErrorEvent("A run is already in progress")
        return

    # 2. 生成 run_id，建立 lease
    run_id = f"run_{uuid4().hex[:12]}"
    self.active_run_id = run_id

    try:
        # 3. Diff 基线（必须在模型/工具可能改文件之前）
        self.diff_tracker.snapshot_before_run()

        # 4. 构建 working memory + memory_block（per-turn，不持久化）
        self.memory_manager.build_working_memory(...)
        memory_block = self.memory_manager.get_context_block()

        # 5. 创建 Engine
        engine = Engine(model, workspace, tools, context_manager,
                        permission_checker, policy_checker, ...)

        # 6. Engine 循环（yield 事件）
        async for event in engine.run_turn(user_message, memory_block=memory_block):
            event = {"run_id": run_id, **event}
            # 事件先写 trace，再发 session_event_bus，再 yield WebSocket
            if event["type"] != "text_delta":
                self.run_store.append_trace(run_id, event)
            self.session_event_bus.emit(event["type"], event)
            yield event
            # 顺便 surface plan mode 变化
            if self.runtime_mode != prev_mode:
                yield RuntimeModeChangedEvent(...)

        # 7. Engine 返回后，Runtime 继续生成终态证据
        self.diff_tracker.snapshot_after_run(run_id)
        diff = self.diff_tracker.write_diff_artifact(run_id)
        yield WorkspaceDiffReadyEvent(run_id=run_id, changed_files=diff.summary)
        yield RunFinishedEvent(run_id=run_id, status="completed", ...)

    except Exception as exc:
        yield RunFinishedEvent(run_id=run_id, status="error", ...)
    finally:
        # 8. lease 必须释放，不管成功/异常/aclose
        self.active_run_id = None
        self._save_session()
```

这里有几个关键设计：

### Diff snapshot 由 Runtime 管理，不是 Engine

`snapshot_before_run()` 必须在 Engine 可能修改文件之前；`snapshot_after_run()` 必须在 Engine 返回之后。如果让 Engine 管 diff，Engine 异常时 diff 状态会不一致。

### `finally` 释放 lease

WebSocket 断开触发 `GeneratorExit`，async generator 被提前 `aclose()`。如果 lease 不在 `finally` 里释放，session 会卡死（`active_run_id` 永远不 None）。

`finally` 里不能 yield（async generator 限制），只做状态修改。

### `run_finished` 是 Runtime 的职责，不是 Engine 的

`final` 事件只表示"模型说完了"，但 diff 可能还没生成、trace 可能还没 flush。`run_finished` 表示"diff、trace、状态都收尾了"。前端只在 `run_finished` 后才刷新 run history。

## Engine.run_turn 的伪代码

```python
async def run_turn(self, user_message, skill_prompt=None, memory_block=None):
    # 1. 把 user message 写进 history
    if self.append_user:
        self.history.append({"role": "user", "content": user_message, ...})

    while tool_steps < max_steps and attempts < max_steps + 2:
        # 2. 检查 stop
        if self.should_stop():
            yield CancelledEvent; return

        # 3. 构建 prompt（ContextManager 管 budget）
        prompt, metadata = self.context_manager.build(
            user_message, history=self.history, tools=self._tool_descriptions(),
            workspace_reminders=..., skill_prompt=skill_prompt,
            memory_block=memory_block,
        )
        yield ModelRequestedEvent(prompt_chars=metadata["prompt_chars"])

        # 4. 调模型（支持 astream/complete/ainvoke 三种路径）
        raw = await self._call_model(prompt)
        yield ModelParsedEvent(...)

        # 5. 解析输出（legacy XML 协议）
        kind, payload = parse(raw)

        if kind in {"tool", "tools"}:
            for tool_payload in ([payload] if kind == "tool" else payload):
                # 6. 重复检测：(name, serialized_args) 连续 3 次相同 -> 停
                sig = (tool_name, json.dumps(tool_args, sort_keys=True))
                if sig == last_tool_signature:
                    repeat_count += 1
                    if repeat_count >= MAX_REPEAT:
                        yield FinalEvent("检测到工具重复调用")
                        return
                else:
                    repeat_count = 0
                    last_tool_signature = sig

                # 7. 委托 ToolExecutor
                async for event in self._execute_tool_payload(tool_payload):
                    yield event
                tool_steps += 1
            continue  # 回到 while 顶部，带新 history 再请求模型

        if kind == "retry":
            # 8. 协议纠正，最多 2 次
            if protocol_retries >= MAX_PROTOCOL_RETRIES:
                yield FinalEvent("模型连续返回无法执行的格式"); return
            protocol_retries += 1
            protocol_correction = str(payload)
            yield RetryEvent(...); continue

        if kind == "final":
            # 9. 模型给出最终回答
            self.history.append({"role": "assistant", "content": final, ...})
            yield FinalEvent(content=final); return

    # 10. step 超限
    yield StepLimitEvent(...)
```

## 两套预算

Engine 维护两个独立计数器：

| 计数器 | 含义 | 上限 |
| --- | --- | --- |
| `attempts` | 调用模型的次数（含 retry） | `max_steps + 2` |
| `tool_steps` | 真正执行工具的次数 | `max_steps`（默认 50） |

为什么分开：模型输出格式错误消耗 `attempts` 但不应假装执行了工具。`max_steps + 2` 的 +2 是给协议纠正留的余量。

## XML 协议 vs 原生 tool calling

### legacy XML 协议

模型输出：
```xml
<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>
<final>已修正 README.md 中的错字。</final>
```

`core/coding/engine/model_output.py::parse()` 从文本中提取标签，归为 `tool` / `tools` / `final` / `retry`。

**优点**：不依赖 provider 的 function calling 支持，任何能输出文本的模型都能用。
**缺点**：模型输出格式不规范时容易进 retry；流式输出时需要 `_visible_final_delta()` 过滤掉 `<tool>` JSON 不让它闪现在聊天界面。

### deerflow_v2 原生 tool calling

```python
from langchain.agents import create_agent
agent = create_agent(model, tools, middleware=middleware_chain, state_schema=SageThreadState)
```

LangGraph 的 `create_agent` 用 provider 原生 tool calling（OpenAI/Anthropic/Gemini 都支持），模型直接返回结构化 `tool_calls`，不需要文本解析。

**优点**：协议稳定，不依赖模型输出格式规范；provider 优化过的 tool calling 体验更好。
**缺点**：要求 provider 支持结构化 tool calling。不支持 provider 只能用 `legacy`。

### 流式输出的过滤

legacy 的流式输出有个坑：模型可能先吐出 `<tool>` 的 JSON，再吐出 `<final>` 的文字。如果直接转发 raw chunk，工具 JSON 会闪现在聊天界面。

`Engine._visible_final_delta()` 只转发 `<final>` 标签内的文字：

```python
def _visible_final_delta(raw, emitted_chars):
    open_tag = "<final>"
    close_tag = "</final>"
    start = raw.find(open_tag)
    if start < 0:
        return "", emitted_chars
    # 如果 <tool 在 <final 之前，不转发
    tool_start = raw.find("<tool")
    if 0 <= tool_start < start:
        return "", emitted_chars
    # 只转发 final 标签内的可见文字
    ...
```

deerflow_v2 不需要这个过滤，因为原生 tool calling 的 tool call 和 final text 是分开的 message block。

## v2 的 Middleware 链怎么替代 Engine 的逻辑

legacy 的 Engine 里散落着很多横切逻辑：重复检测、协议纠正、stop 检查、上下文压缩触发。这些在 v2 里被拆成独立的 middleware：

| legacy Engine 逻辑 | v2 Middleware |
| --- | --- |
| `should_stop()` 检查 | `ThreadContextMiddleware` 注入 stop 信号 |
| 重复工具检测 | `LoopDetectionMiddleware`（设计，未实现） |
| 协议纠正 retry | `ProviderErrorMiddleware` + `ToolErrorMiddleware` |
| `before_model_request` 压缩触发 | `SummarizationMiddleware`（设计，未实现） |
| `DurableContextMiddleware` | 投影 summary/goal/delegation/skill/memory |
| `TokenBudgetMiddleware` | 单 run token 预算 |

每个 middleware 声明：
- 作用 hook（before_model / after_model / before_tool / after_tool）
- 读写的 state channel
- fail 是 closed（终止 run）还是 open（留错误事件继续）
- 产生的 timeline event
- 与相邻 middleware 的顺序测试

**权限、审批、路径安全、owner、revision 必须 fail-closed**。遥测、标题、非关键摘要可以降级，但必须留错误事件。

## Engine 不创建 session、不写 diff、不管 WebSocket

这是 Sage 的核心边界。Engine 只负责推进 turn 循环，状态落点全部由 Runtime 决定：

| 状态 | 谁写 | 在哪 |
| --- | --- | --- |
| session history | Engine 通过 `append_history` 回调 | Runtime 持有的 session 对象 |
| tool result history | Engine 通过 `_append_tool_history` | Runtime 持有的 history list |
| run trace | Runtime 在 yield 事件时 | `evidence/<session>/<run>/trace.jsonl` |
| diff artifact | Runtime 在 Engine 返回后 | `evidence/<session>/<run>/diff.json` |
| timeline event | Runtime 通过 `session_event_bus.emit` | `timeline.sqlite3` |
| memory | MemoryManager（Runtime 持有） | `memory/<workspace>/state.json` |

这样 Engine 可以被独立测试（`ScriptedApiClient` 预写模型响应），不需要构造完整 Runtime。

## 模型客户端契约

`ApiClient` Protocol 的最小要求：

```python
class ApiClient(Protocol):
    async def complete(self, prompt: str) -> str: ...
```

实际 Engine 兼容三种调用路径：

| 路径 | 方法 | 用途 |
| --- | --- | --- |
| `astream(messages)` | 逐块读取，产生 `text_delta` | 真实 provider 流式 |
| `complete(prompt)` | 一次性返回 | ScriptedApiClient 和简单 provider |
| `ainvoke(messages)` | LangChain 风格 | LangChain `BaseChatModel` |

`_build_ainvoke_messages()` 按 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 把 prompt 拆成 system + user 两条消息（详见 [Context 组装与 Prompt Caching](04-context-prompt-caching.md)）。

## 终态归一

v2 runtime 的终态必须归一为：

```text
succeeded
failed
cancelled
interrupted
budget_exhausted
```

**不允许没有 assistant 内容、没有明确错误、却标记成功的静默终态。** 这是 `TerminalResponseMiddleware` 的职责（工具结束后禁止静默成功）。

`interrupted` 是进程重启后的恢复状态，`retryable=True`，不伪造自动恢复成功。

## 外部参考的使用边界

XML 协议与原生 tool calling 在本章中只用于解释 Sage 自己的迁移历史。不能仅凭界面或
旧版本文章推断其他 Agent 的 runtime 分层，也不使用 middleware 数量衡量架构成熟度。
判断 Sage 是否正确，应检查职责所有权、失败终态和测试，而不是比较模块数量。

## 第一入口

按顺序打开：

1. `core/coding/runtime.py::CodingRuntime.run_turn` - legacy run 生命周期
2. `core/coding/engine/engine.py::Engine.run_turn` - legacy turn 循环
3. `core/coding/engine/model_output.py::parse` - XML 协议解析
4. `core/harness/runtime_adapter.py::SageHarnessRuntimeAdapter` - v2 适配
5. `packages/sage_harness/sage_harness/agents/factory.py::create_sage_agent` - v2 工厂
6. `packages/sage_harness/sage_harness/middleware/registry.py` - 默认 8 个 middleware 及顺序

## 测试证据

- `tests/core/coding/test_agent_loop.py` - Engine 循环 + 重复检测 + retry
- `tests/core/coding/test_engine.py` - Engine 单元测试
- `tests/core/coding/test_runtime_run_lifecycle.py` - run 生命周期 + lease + run_finished
- `tests/core/coding/test_model_output.py` - XML 协议解析
- `tests/harness/test_agent_factory.py` - v2 agent 工厂
- `tests/harness/test_middleware_order.py` - middleware 顺序

## 当前边界

> [!warning] v2 runtime 的 middleware 与 adapter 职责仍需持续校正
> - 默认 registry 当前是 8 个最小安全 middleware，不以历史设计稿的数量作为完成标准
> - ApprovalInterrupt、自动 Summarization 和 LoopDetection 尚未全部落在原生 middleware hook
> - 部分策略继续由 ToolExecutor、ToolPolicyChecker、adapter 与外层 Runtime 承担
> - `deerflow_v2` 已是新会话默认 profile；legacy 继续服务历史 session 与对等回归

## 自测

1. `CodingRuntime.run_turn` 和 `Engine.run_turn` 各自负责什么？为什么不能合并？
2. `final` 和 `run_finished` 的区别？为什么前端要等 `run_finished` 才刷新？
3. lease 为什么必须在 `finally` 里释放？如果不释放会怎样？
4. legacy XML 协议和原生 tool calling 的优缺点？为什么 Sage 两者都保留？
5. 两套预算（attempts / tool_steps）为什么不能共用一个计数器？
6. v2 的 middleware 链比 legacy 的 Engine 横切逻辑好在哪里？代价是什么？

下一章：[Context 组装与 Prompt Caching](04-context-prompt-caching.md)
