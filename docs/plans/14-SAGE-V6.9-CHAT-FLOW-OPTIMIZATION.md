# Sage V6.9 Chat Flow 优化计划

> **For Codex Agent:** 前端 chat 流程优化,基于 Hermes Studio 差距分析。
> 可在 `dev/sage-v6` 分支直接开发,或创建 `codex/chat-flow-optimization` 分支。

**Goal:** 优化前端 chat 体验:思考指示器即时反馈、聊天区元素居中对齐、工具参数/结果完整展示、流式打字光标。

**Architecture:** 纯前端改动,不依赖后端变更。后端建议改动在最后标注。

---

## Task 1: 思考反馈速度优化 (P0)

**根因**: `sendMessage()` 把 `thinkingPhase = ''`,导致点击发送后到收到 `turn_started` 事件之间 ThinkingIndicator 不显示,用户看到空白无反馈。

**时间线**:
```
用户点击发送
  → sendMessage(): isThinking=true, thinkingPhase='' → showThinkingIndicator=false (不显示!)
  → WebSocket 网络延迟
  → 后端收到消息 → TurnStartedEvent 发射
  → applyCodingEvent('turn_started'): thinkingPhase='思考中' → 现在才显示
```

从发送到显示之间存在 WebSocket 往返延迟,用户体验为"点击后没反应"。

**文件**: `frontend/src/stores/coding.ts`, 第 768 行

**修复**: 将 `thinkingPhase.value = ''` 改为 `thinkingPhase.value = '思考中'`

**效果**: 用户点击发送后**立刻**看到"思考中"三点动画,后续事件自然更新为"正在请求模型...""正在执行工具..."等。

**进阶(可选)**: 加一个计时器,显示"思考中 (3s)"之类的时间计数。后端 `run_finished` 事件有 `duration_ms`,但这个不合适(是整个 run 的时长)。前端可以自己计数每秒递增。

---

## Task 2: 模型调用过程对齐修复 (P0)

**根因**: 多个元素的 CSS margin 与父级 `.message-area > * { max-width:880px; margin:auto }` 冲突:

| 元素 | 文件:行 | 问题 |
|------|---------|------|
| `CodingThinkingIndicator` | ThinkingIndicator.vue:24 | `margin: 0 0 12px` 覆盖 `margin:auto`,左对齐 |
| `CodingExecutionLog` | ExecutionLog.vue:52 | `max-width:760px` 无 `margin:auto`,不居中 |
| `.process-details` | CodingView.vue:437 | `margin:-9px 0 16px 40px` 的 `margin-left:40px` 缩进过深 |

**修复**:

1. `CodingThinkingIndicator.vue` 第 24 行 -- `margin: 0 0 12px` 改为 `margin: 0 auto 12px`
2. `CodingExecutionLog.vue` 第 52 行 -- `margin: 0 0 5px` 改为 `margin: 0 auto 5px`
3. `CodingView.vue` 第 437 行 -- `.process-details { margin:-9px 0 16px 40px; ... }` 改为 `margin:-9px auto 16px auto;`

**注意**: CodingView.vue 的 CSS 是压缩在一行的,需要精确定位字符串替换。

---

## Task 3: 工具参数完整展示 (P0)

**根因**: `toolSummary()` 只提取 path/pattern/command 等拼一句话摘要。原来的 `.tool-args` CSS 类只用于 inline 省略(`white-space:nowrap;overflow:hidden`),是死代码。完整 args 从未展示。

**文件**: `frontend/src/components/coding/chat/CodingToolActivity.vue`

**修复**: 在 `tool-result` 区域内,`<pre>` diff 内容**之前**,新增一个可折叠的工具参数 JSON 块:

```html
<div v-if="tool.content" class="tool-result">
  <!-- 新增: 工具参数折叠展示 -->
  <details v-if="Object.keys(tool.args || {}).length > 0" class="tool-args-details">
    <summary class="tool-args-summary">查看完整参数</summary>
    <pre class="tool-args-full">{{ JSON.stringify(tool.args, null, 2) }}</pre>
  </details>
  <!-- 原有: diff 内容 -->
  <pre>...</pre>
</div>
```

**CSS** (追加在 `.tool-args` 已有规则之后):

```css
.tool-args-details {
  margin: 4px 0 8px;
}
.tool-args-details .tool-args-full {
  color: var(--sage-text-secondary);
  font-family: var(--sage-font-mono);
  font-size: 11px;
  overflow-x: auto;
  white-space: pre;
  max-height: 200px;
  padding: 6px 8px;
  margin: 4px 0 0;
  border-radius: 4px;
  background: var(--sage-surface-muted);
}
.tool-args-summary {
  cursor: pointer;
  font-size: 11px;
  color: var(--sage-text-muted);
}
```

**注意**: 不要在 `tool-args-details` 内部复用现有的 `.tool-args` class(该 class 有 `white-space:nowrap`),新建 `.tool-args-full`。

---

## Task 4: 工具结果超长展开 (P0)

**根因**: `codingEvents.ts` 在 `updateToolActivity` 和 `appendSettledToolActivity` 中对结果 `content.slice(0, 2000)` 截断。`CodingToolActivity.vue` 的 `resultPreview` 已支持 800 字符预览 + 展开按钮,但原始数据已被截断。

**文件**: `frontend/src/stores/codingEvents.ts`

**修复**: 删除两处 `.slice(0, 2000)`:
- 第 359 行: `target.content = event.content.slice(0, 2000)` → `target.content = event.content`
- 第 382 行: `content: event.content.slice(0, 2000),` → `content: event.content,`

CodingToolActivity 已有展开/收起逻辑(`expandedResults` Set, `resultPreview` 函数, `toggleResult` 函数),无需额外改动。

---

## Task 5: ThinkingIndicator 在工具阶段保持显示 (P1)

**根因**: `showThinkingIndicator` computed (CodingView.vue:47-51) 中有 `!(last?.tools?.length)` 条件,一旦 tool_call 到达就隐藏 ThinkingIndicator。用户看不到"正在执行工具..."等状态。

**文件**: `frontend/src/views/CodingView.vue`, 第 47-51 行

**当前**:
```typescript
const showThinkingIndicator = computed(() => {
  if (!store.isThinking || !store.thinkingPhase) return false
  const last = store.messages[store.messages.length - 1]
  return !(last?.tools?.length)  // ← 问题: tool_call 到达后隐藏
})
```

**修复**:
```typescript
const showThinkingIndicator = computed(() => {
  if (!store.isThinking || !store.thinkingPhase) return false
  return true  // 只要有 thinkingPhase 就显示,不管有没有 tools
})
```

**效果**: 执行日志和 ThinkingIndicator 同时展示,用户能看到"正在执行工具...""等待工具确认..."等持续反馈。

---

## Task 6: 流式打字光标 (P1)

**文件**: `frontend/src/components/coding/chat/CodingMessageTurn.vue` + `frontend/src/style.css`

**修复**:

1. `style.css` 末尾追加全局动画:
```css
@keyframes sage-cursor-blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}
```

2. `CodingMessageTurn.vue` 的 template 中,消息内容后追加光标:
```html
<article v-if="message.content" class="message-content-shell">
  <div class="message-content" v-html="renderedContent"></div>
  <span v-if="message.isThinking" class="streaming-cursor" aria-hidden="true">▌</span>
</article>
```

3. `CodingMessageTurn.vue` 的 style 末尾追加:
```css
.streaming-cursor {
  display: inline;
  color: var(--sage-text);
  font-weight: 300;
  animation: sage-cursor-blink 1s ease infinite;
}
```

---

## 需要提醒 codex 的后端改动 (本次不做)

这些需要后端配合,当前前端分析发现的后端缺口:

| # | 文件 | 改动 | 目的 |
|---|------|------|------|
| 1 | `engine.py:192` | astream 循环中读 `getattr(chunk, "reasoning_content", "")` | 提取模型推理(token 级) |
| 2 | `events.py` | 新增 `ReasoningDeltaEvent(type="reasoning_delta", delta=str)` | 推理事件类型 |
| 3 | `executor.py:49` | ToolCallEvent 前后计时间,写入 `ToolResultEvent.duration_ms` | 工具执行时长 |
| 4 | `events.py` | `ToolResultEvent` 新增 `duration_ms: int = 0` | 时长字段 |
| 5 | `api/coding.py` | 新增 `GET /session/{id}/timeline` REST 端点 | 前端恢复执行过程 |

---

## 测试

每个 Task 完成后运行:
```bash
cd frontend && npx vitest run --environment jsdom
cd frontend && npx vue-tsc --noEmit -p tsconfig.app.json
```

全量完成后:
```bash
npm run build
```

---

## 执行顺序

Task 1 和 Task 2 可以同时做(不同文件)。Task 3-6 相互独立,可以并行。
