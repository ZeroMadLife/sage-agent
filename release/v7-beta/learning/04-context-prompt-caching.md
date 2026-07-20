# 04 - Context 组装与 Prompt Caching

> Last verified against: `dev/sage-v7@23a0090` (2026-07-20)

> 本章目标：能画出 Sage 的 prompt 五段式拼装顺序，解释三层 system prompt 怎么命中 provider prefix cache，讲清压力分级（normal/budget/snip/compact/high/emergency）的触发条件和行为。

## Prompt 不是 messages list

最小 agent 的 context 通常只是消息列表加一个裁剪函数。Sage 的 context 要处理更多来源：

```text
prefix (system prompt 三层)
  ├── stable 层: 身份定义 + 行为准则 + 工具列表
  ├── __SYSTEM_PROMPT_DYNAMIC_BOUNDARY__  ← cache 边界标记
  ├── context 层: workspace reminders + deferred tools
  └── volatile 层: "Session date: 2026-07-20"

skill_prompt (<skill-instructions>...</skill-instructions>)  ← per-turn，不持久化
memory_block (<working-memory> + <durable-memory>)           ← per-turn，不持久化
history (Transcript: user/assistant/tool 交替)
current_request (Current user request: ...)
```

`ContextManager.build()` 按固定顺序组装这五段。**顺序不能随意调整**，因为它影响 provider prefix cache 的命中率。

## 三层 system prompt 与 cache 命中

### 为什么分三层

Provider（OpenAI/Anthropic）有 prefix cache：如果 prompt 开头 N 个 token 完全相同，缓存命中，只对剩余部分计费。

Sage 的三层分离就是为了让 stable 层在多轮对话中保持完全一致：

```text
┌─────────────────────────────────────────┐
│  Stable Layer (不变层)                   │
│  - 身份定义 ("You are Sage...")           │
│  - 行为准则 (inspect before edit...)      │
│  - 输出协议 (<tool> JSON / <final>)       │
│  - Available tools 列表                  │
│                                         │  ← 同一天内不变，命中 provider prefix cache
├─────────────────────────────────────────┤
│  __SYSTEM_PROMPT_DYNAMIC_BOUNDARY__     │  ← 缓存边界标记
├─────────────────────────────────────────┤
│  Context Layer (会话层)                  │
│  - "Project context: current workspace" │
│  - <system-reminder> workspace_reminders│  ← SAGE.md / AGENTS.md 内容
│  - Deferred tools 列表                  │  ← tool_search 可激活的工具名
│                                         │  ← 工具变化时失效
├─────────────────────────────────────────┤
│  Volatile Layer (易变层)                 │
│  - "Session date: 2026-07-20"           │  ← 每天变化
│                                         │
└─────────────────────────────────────────┘
```

- **stable 层**：身份 + 工具列表，只要 `activated_tools` 不变就稳定。
- **context 层**：workspace reminders + deferred tools，工具激活/停用时失效。
- **volatile 层**：只有日期，同一天内不变。**故意用日期精度而不是时间精度**，这样同一天的多轮对话 volatile 层完全一致，cache 不失效。

### boundary 标记的作用

`__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__` 是一个固定字符串，作为 cache 边界标记。`Engine._build_ainvoke_messages()` 用它把 prompt 切成两条消息：

```python
def _build_ainvoke_messages(prompt: str) -> list[dict[str, str]]:
    boundary_index = prompt.find(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
    if boundary_index == -1:
        return [{"role": "user", "content": prompt}]  # fallback
    system_content = prompt[:boundary_index].strip()   # boundary 之前 -> system
    user_content = prompt[boundary_index + len(boundary):].strip()  # 之后 -> user
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]
```

boundary 之前（stable 层）作为 `role: system`，provider 会缓存。boundary 之后（context + volatile + skill + memory + history + current_request）作为 `role: user`，每轮变化。

### 缓存失效条件

`_build_system_prompt_once()` 用两个条件判断是否重建 system prompt：

```python
tools_key = tuple([*tools, DYNAMIC_BOUNDARY, *reminders, *deferred])
if (
    self._cached_system_prompt is not None
    and not self._system_prompt_dirty
    and self._cached_tools_key == tools_key
):
    return self._cached_system_prompt  # 命中缓存
# 否则重建
```

失效条件：
1. `_system_prompt_dirty = True`（手动 invalidate，比如 `CompactManager.compact()` 后）
2. `tools_key` 变化（工具列表/workspace reminders/deferred tools 变了）

## 预算分配（字符 -> token 的演进）

### V6 的字符预算

```python
class ContextManager:
    def __init__(self, total_budget: int = 60000, ...):
        self.total_budget = total_budget  # 字符
```

`_render_to_budget()` 的分配逻辑：

```python
# 预留：current_request + skill_prompt + memory_block + 分隔符
reserved = len(current) + len(skill) + len(memory) + 8
remaining = max(0, total_budget - reserved)

# 剩余的 1/3 给 prefix，2/3 给 history
prefix_budget = remaining // 3
history_budget = remaining - prefix_budget

# 如果还超，先砍 history 的尾部，再砍 prefix 的尾部
```

`tail_clip()` 保留尾部（最近的对话），砍掉头部（最早的对话）。

**问题**：
- 字符 != token。中文一个字 3 字节但约 1-2 token，英文 1 字符约 0.25 token。字符预算对中文偏保守，对英文偏准确。
- `tail_clip` 是 lossy 不可恢复的。早期对话被砍掉后，模型不知道之前发生了什么。

### V6.6+ 的 token 预算

V6.6 引入 `ContextPolicy` + `TokenCounter`：

```python
@dataclass(frozen=True)
class ContextPolicy:
    context_window_tokens: int          # 模型窗口（如 200000）
    output_reserve_tokens: int = 20_000  # 输出预留
    budget_ratio: float = 0.50
    snip_ratio: float = 0.60
    compact_ratio: float = 0.65
    high_ratio: float = 0.70
    cache_override_ratio: float = 0.75
    emergency_ratio: float = 0.85

    @property
    def effective_limit_tokens(self) -> int:
        return self.context_window_tokens - self.output_reserve_tokens
```

`TokenCounter.count()` 先尝试模型自带的 `get_num_tokens()`，失败则 fallback 到 `UTF-8 bytes / 4`（保守估计），并标记 `estimated=True`。前端会展示这个标记。

## 压力分级（6 个阶段）

| Stage | Ratio | Behavior |
| --- | ---: | --- |
| Normal | `< 0.50` | 保持原样不动 |
| Budget | `>= 0.50` | 历史 tool preview 限 30000 字符 |
| Snip | `>= 0.60` | 去重旧 read/search，保留最新 3 个 tool result |
| Auto compact | `>= 0.65` at turn boundary | LLM 生成结构化摘要，重建 active history |
| High pressure | `>= 0.70` mid-turn | tool preview 限 15000 字符 |
| Emergency | `>= 0.85` | 停止，等下轮压缩，不允许下一次 model call |

### 关键约束：mid-turn 只能做确定性裁剪

**语义压缩只在 turn boundary（新用户消息进来、第一个 model request 之前）做。** mid-turn 只能做确定性裁剪（去重、截断 preview）。

为什么：mid-turn 摘要会把当前正在执行的工具链腰斩。比如模型刚 read_file 准备 patch_file，mid-turn 摘要把 read_file 的结果压没了，patch_file 就会失败。

### CompactManager 的结构化摘要

V6.6 的 `CompactManager` 生成结构化摘要：

```python
class CompactionSummary(BaseModel):
    goal: str
    user_constraints: list[str]
    decisions: list[str]
    completed_work: list[str]
    active_todos: list[str]
    files_read: list[str]
    files_modified: list[str]
    tests: list[str]
    errors: list[str]
    artifact_refs: list[str]
    next_steps: list[str]
    source_transcript_range: tuple[int, int]
    source_run_ids: list[str]
```

摘要的第一行固定：`Historical handoff only; the latest user message always wins.`（历史摘要只是交接，最新用户消息永远优先）。

### 断路器

连续两次压缩节省 < 10% 就禁用自动压缩，发 warning：

```python
# 两次无效压缩 -> 禁用 auto compaction
if savings_ratio < 0.10:
    consecutive_ineffective += 1
    if consecutive_ineffective >= 2:
        auto_compaction_disabled = True
        yield WarningEvent("auto compaction disabled: ineffective")
```

这防止"压缩了半天没省多少 token 还浪费 LLM 调用"。

## skill_prompt 和 memory_block 为什么不写 history

```python
# ContextManager.build()
raw_sections = {
    "prefix": system_prompt,
    "history": self._render_history(history),
    "current_request": f"Current user request:\n{user_message}",
}
if skill_prompt:
    raw_sections["skill_prompt"] = f"<skill-instructions>\n{skill_prompt}\n</skill-instructions>"
if memory_block:
    raw_sections["memory"] = memory_block
```

`skill_prompt` 和 `memory_block` 是 **per-turn 注入，不持久化到 history**。每轮结束后它们消失，下一轮重新构建。

为什么：
- skill prompt 是 slash command 展开（如 `/review` 展开成"审查代码..."），如果写 history 会导致 history 里出现大段指令文本，浪费 token。
- memory block 是 working memory + durable recall，每轮内容不同。如果写 history 会导致旧 memory 永远在 history 里。
- 两者都通过 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 之后注入，不影响 stable 层 cache。

## v2 的 DurableContextMiddleware

v2 runtime 里，context 投影由 `DurableContextMiddleware` 负责：

```python
class DurableContextMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    # 投影 summary_text / goal / delegation / skill_context / memory_refs
    # 所有内容带不可信边界和 token budget
```

它把 `SageThreadState` 里的 durable channel 投影成模型可见的 context block。和 legacy 的 `ContextManager.build()` 不同的是：

- v2 的 `summary_text` 是独立 channel，不伪装成聊天消息
- v2 的 `memory_refs` 只保存稳定引用，长期事实仍由 MemoryStore 持有
- v2 的 `surface_context` 在 run 开始时由 API 验证并冻结

## 大工具结果归档

V6.6+ 引入 `ToolResultStore`：>16KB 的 tool result 写到 `evidence/<session>/runs/<run_id>/tool-results/<call_id>.txt`，active history 只存 preview（前 120 行 + 后 80 行，上限 12000 字符 + `[full result: {call_id}]` 引用）。

```python
class ToolResultStore:
    PERSIST_THRESHOLD_BYTES = 16 * 1024
    PREVIEW_LINES = 200
    PREVIEW_CHARS = 12_000

    def archive(self, call_id: str, content: str) -> ArchivedToolResult:
        # 写完整内容到文件
        path = self.root / f"{call_id}.txt"
        path.write_text(content)
        # 生成 preview
        preview = self._bounded_preview(content, call_id)
        return ArchivedToolResult(call_id, path, preview, ...)
```

这样大工具结果（如 `grep -r` 输出 50KB）不会塞满 model context，但通过 `call_id` 可以随时取回全文。

## 外部参考的使用边界

Prompt cache、token 计算和压缩语义会随模型 Provider 与 SDK 版本变化。外部实现只作为
问题来源，不能据此断言某产品“没有”缓存或断路器。本章对 Sage 的结论必须能回到
Context controller、middleware、usage 记录和对应测试。

## 第一入口

按顺序打开：

1. `core/coding/context/manager.py::ContextManager.build` - legacy prompt 组装
2. `core/coding/context/manager.py::_build_system_prompt_once` - 三层 prefix + cache
3. `core/coding/context/manager.py::_render_to_budget` - 字符预算分配
4. `core/coding/context/budget.py::ContextPolicy` - token 压力分级
5. `core/coding/context/compact.py::CompactManager` - 结构化压缩
6. `core/coding/persistence/tool_result_store.py::ToolResultStore` - 大结果归档
7. `packages/sage_harness/sage_harness/middleware/durable_context.py` - v2 context 投影

## 测试证据

- `tests/core/coding/test_context_compact.py` - compact + 预算
- `tests/core/coding/test_context_budget.py` - token 压力分级
- `tests/core/coding/test_context_projection.py` - 不可变投影
- `tests/core/coding/test_context_compactor.py` - 结构化摘要 + 断路器
- `tests/core/coding/test_tool_result_store.py` - 大结果归档
- `tests/core/coding/test_engine.py::test_engine_ainvoke_splits_system_and_user_messages` - boundary 拆分

## 当前边界

> [!warning] Context 组装在 legacy 和 v2 之间有差距
> - legacy 用 `ContextManager`（字符预算 + `tail_clip`）
> - v2 使用 token budget；只有显式选择的 legacy session 继续使用字符预算
> - v2 的 `SummarizationMiddleware` 设计完成但实现未完成
> - mid-turn emergency stop 在 legacy 里通过 `before_model_request` 回调实现，v2 还没接
> - 大工具结果归档已进入新运行链；legacy 行为仍需单独回归

## 自测

1. 三层 system prompt 各自为什么这样分？为什么 volatile 层用日期精度而不是时间精度？
2. `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 标记的作用？如果没有它会怎样？
3. 压力分级的 6 个阶段各自做什么？为什么 mid-turn 不能做语义压缩？
4. 断路器（连续 2 次无效压缩禁用）解决什么问题？
5. `skill_prompt` 和 `memory_block` 为什么不写 history？
6. 大工具结果归档（>16KB 外存）为什么不能直接 inline 到 history？

下一章：[Tool Registry 与工具系统](05-tools-registry.md)
