# Sage v5.1 规划：Plan Mode 对接 + 前端 Skill 菜单

> 日期：2026-07-09
> 分支：`dev/sage-v4`
> 前置：V5.0 结构解耦重构已完成（357 tests + 70 frontend tests 全绿）
> 参考：[Hermes Studio](https://github.com/EKKOLearnAI/hermes-studio) - Vue 3 + Naive UI + Socket.IO 架构

## 目标

V5.1 聚焦两件事：

1. **Plan Mode 前后端打通** — 让用户能在 Web 端进入/退出计划模式，看到 plan 文件，审批从 plan 到执行的过渡。
2. **前端 `/` 触发 Skill 菜单** — 输入框打 `/` 弹出可用 skill 列表，选中后展开为 slash command。

本轮不做：Naive UI 全量迁移、Monaco 编辑器、xterm 终端、delegate 子 agent 系统（留给 V4 tool system 强化）。

---

## 现状分析

### 后端已有的 Plan Mode 基础设施

| 组件 | 文件 | 状态 |
|------|------|------|
| `PlanModeManager` | `core/coding/plan_mode.py` | ✅ enter/exit + 路径校验 + session 持久化 |
| `runtime.enter_plan_mode()` / `exit_plan_mode()` | `core/coding/runtime.py:228-244` | ✅ API 就绪 |
| `enter_plan_mode` / `exit_plan_mode` tool | `core/coding/tools/plan_tools.py` | ✅ deferred tool 已注册 |
| plan mode 写保护 | `core/coding/tool_executor/permissions.py:61-67` | ✅ 只允许 read_only 工具 |
| `runtime_mode_changed` 事件 | `core/coding/runtime.py:234` | ⚠️ emit 到 SessionEventBus（JSONL 文件），**不走 WebSocket** |
| plan 文件读取 | — | ❌ 没有专门的 API 读取 plan 文件内容 |
| plan 文件创建 | `PlanModeManager.enter()` 创建目录但不创建文件 | ❌ 文件由 LLM 在 plan mode 中通过 write_file 创建 |

### 后端已有的 Skill 基础设施

| 组件 | 文件 | 状态 |
|------|------|------|
| `SkillRegistry` | `core/coding/skills/registry.py` | ✅ 发现在线 skill + bundled skill |
| `GET /api/v1/coding/skills` | `api/coding.py:327` | ✅ 返回 skill metadata 列表 |
| `GET /api/v1/coding/skills/{name}` | `api/coding.py:346` | ✅ 返回 skill 内容 |
| `runtime.resolve_slash()` | `core/coding/runtime.py:209` | ✅ 解析 `/travel 杭州 3天` → 展开为 prompt |
| WebSocket slash 处理 | `api/coding.py:178-186` | ✅ 收到 `/skill args` 先 resolve 再喂给 engine |
| 前端 `/` 菜单 | — | ❌ 完全没有 |

### 核心缺口

1. **`runtime_mode_changed` 事件不达前端** — `SessionEventBus.emit()` 只写 JSONL 文件，`run_turn()` 的 WebSocket stream 只 yield engine 事件。plan mode 切换发生在 tool 执行期间（`enter_plan_mode` tool 调用 `runtime.enter_plan_mode()`），该事件无法到达前端。
2. **plan 文件无读取 API** — 前端无法查看 plan 文件内容，也不知道 plan 文件路径。
3. **前端无 plan mode UI** — 没有模式指示器、没有 plan 文件预览面板。
4. **前端无 `/` skill 菜单** — 输入框纯文本，不感知 `/` 输入。

---

## 任务分解

### Task 1 (P0)：WebSocket 通道补全 — runtime_mode 事件透传

**后端改动，无需前端配合。**

**问题：** `runtime_mode_changed` 事件 emit 到 `SessionEventBus`（JSONL），但 WebSocket stream（`runtime.run_turn()`）只 yield engine 的 `RunEvent` 事件。plan mode 切换发生在 tool 执行内部，engine 不知道。

**方案：** 在 `runtime.py` 的 `run_turn()` 中，engine 每轮 tool 执行后检查 `runtime_mode` 是否变化，若变化则 yield 一个 `runtime_mode_changed` 事件给 WebSocket。

**改动文件：**
- `core/coding/runtime.py` — `run_turn()` 中在每次 engine yield 后检查 mode 变化
- `core/coding/engine/events.py` — 新增 `RuntimeModeChangedEvent`

**改动逻辑（`run_turn` 内）：**
```python
async def run_turn(self, user_message: str) -> AsyncIterator[dict[str, Any]]:
    prev_mode = self.runtime_mode
    # ... engine 循环 ...
    async for event in engine.run_turn(user_message):
        event = {"run_id": run_id, **event}
        # ... 正常 yield ...
        yield event
        # 检查 mode 是否在 tool 执行期间被切换
        if self.runtime_mode != prev_mode:
            yield {"run_id": run_id, "type": "runtime_mode_changed", "mode": self.runtime_mode, ...}
            prev_mode = self.runtime_mode
```

**验收：** 前端 WebSocket 收到 `{"type": "runtime_mode_changed", "mode": "plan", ...}` 事件。

---

### Task 2 (P0)：Plan 文件读取 API

**后端改动，需前端配合消费。**

**问题：** plan 文件由 LLM 在 plan mode 中通过 `write_file` 写入 `.coding/plans/xxx-plan.md`，但前端没有 API 读取它。

**方案：** 复用已有 `GET /api/v1/coding/{session_id}/file?path=...`，前端用 plan_mode 状态中的 `plan_path` 直接调这个接口即可。但需要确认 plan_path 在 runtime_mode 事件中传给前端。

**改动文件：**
- `core/coding/runtime.py` — `run_turn()` yield 的 mode_changed 事件带上 `plan_path` 和 `topic`
- 无需新增 API，复用 `read_file` REST + `read_file` tool

**验收：** 前端拿到 `runtime_mode_changed` 事件后，能从中拿到 `plan_path`，再用 `GET /file?path={plan_path}` 读取 plan 内容。

---

### Task 3 (P0)：前端 — runtime_mode 事件处理 + Plan Mode 指示器

**前端改动，依赖 Task 1/2 完成。**

**方案：** `codingStream.ts` 处理 `runtime_mode_changed` 事件，更新 store 状态；`CodingView.vue` 顶部显示 plan mode 横幅。

**改动文件：**
- `frontend/src/stores/codingStream.ts` — 新增 `runtimeMode` 状态，处理 `runtime_mode_changed` 事件
- `frontend/src/views/CodingView.vue` — 顶部 plan mode 横幅（蓝色条 + topic + plan_path 链接）
- `frontend/src/components/coding/` — 可能新增 `CodingPlanBanner.vue` 组件

**前端需要消费的事件格式（后端保证）：**
```json
{
  "type": "runtime_mode_changed",
  "mode": "plan",
  "topic": "refactor auth module",
  "plan_path": ".coding/plans/refactor-auth-module-plan.md"
}
```

**验收：** 用户说"帮我规划一下重构方案" → LLM 调用 `enter_plan_mode` → 前端顶部出现 plan mode 横幅 → LLM 写 plan 文件 → 用户看到横幅并能点击查看 plan。

---

### Task 4 (P0)：前端 — Plan 文件预览面板

**前端改动，依赖 Task 3 完成。**

**方案：** 点击 plan mode 横幅中的 plan_path 链接，弹出 plan 文件内容预览（Markdown 渲染）。

**改动文件：**
- `frontend/src/views/CodingView.vue` — plan 预览抽屉/弹窗
- `frontend/src/api/coding.ts` — 调用 `GET /api/v1/coding/{session_id}/file?path={plan_path}`

**验收：** plan 文件内容在前端正确渲染为 Markdown。

---

### Task 5 (P1)：前端 — `/` 触发 Skill 菜单

**前端改动为主，后端已就绪。**

**后端现状：** `GET /api/v1/coding/skills` 返回 `[{name, description, source, argument_hint}]`，WebSocket 已处理 `/skill args` → resolve → engine。

**方案：** `CodingComposer.vue` 监听输入，当第一个字符是 `/` 时，弹出 skill 下拉列表（模糊匹配），选中后自动填入 `/skill-name `。

**改动文件：**
- `frontend/src/components/coding/composer/CodingComposer.vue` — `/` 触发逻辑 + 下拉菜单
- `frontend/src/api/coding.ts` — 可能有 `listSkills()` 已存在，确认复用
- `frontend/src/stores/codingStream.ts` — 可选缓存 skills 列表

**交互设计：**
```
用户输入: /
↓ 弹出下拉
┌──────────────────────────────┐
│ /review      代码审查        │
│ /travel      旅游行程规划     │
│ /travel-planning 兼容旧入口   │
└──────────────────────────────┘
用户继续输入: /re
↓ 模糊过滤
┌──────────────────────────────┐
│ /review      代码审查        │
└──────────────────────────────┘
用户选中 → 输入框变为: /review 
用户补充参数 → /review 检查 src/auth.py
用户回车 → 发送
```

**验收：** 输入 `/` 看到全部 skill，输入 `/rev` 过滤到 `/review`，选中后可补参数发送。

---

### Task 6 (P1)：Plan Mode Exit 流程 — LLM 完成计划后提示用户审批

**后端 + 前端改动。**

**问题：** plan mode 下 LLM 写完 plan 文件后调用 `exit_plan_mode` tool 直接退出，用户没有机会审阅 plan 再决定是否进入执行。

**方案：** `exit_plan_mode` tool 改为不直接退出，而是 yield 一个 `plan_ready_for_review` 事件给前端，前端显示审批 UI（"采纳计划并执行" / "继续修改计划" / "放弃"）。用户点"采纳"后前端发 REST 请求 `POST /api/v1/coding/{session_id}/plan/approve`，后端调 `runtime.exit_plan_mode()`。

**改动文件：**
- `core/coding/tools/plan_tools.py` — `exit_plan_mode` 改为返回 plan 内容摘要 + 标记等待审批
- `core/coding/runtime.py` — 新增 `approve_plan()` / `reject_plan()` 方法
- `api/coding.py` — 新增 `POST /api/v1/coding/{session_id}/plan/approve` 和 `/plan/reject`
- `api/schemas.py` — 新增 `PlanApprovalRequest`
- `frontend/src/stores/codingStream.ts` — 处理 `plan_ready_for_review` 事件
- `frontend/src/views/CodingView.vue` — plan 审批 UI

**验收：** LLM 写完 plan → 前端显示 plan 内容 + 审批按钮 → 用户点"采纳" → 退出 plan mode → 后续消息可执行写操作。

---

### Task 7 (P2)：Session 恢复时同步 runtime_mode

**后端改动，无需前端配合。**

**问题：** `CodingRuntime.resume()` 会恢复 plan_mode 状态，但前端 WebSocket 连上后不知道当前是 plan mode。需要在前端连接时返回当前 mode。

**方案：** WebSocket 连接建立后，立即推一个 `runtime_mode_changed` 事件（初始状态同步）。

**改动文件：**
- `api/coding.py` — `coding_stream()` WebSocket handler，`accept()` 后先推当前 mode

**验收：** 刷新页面 / 重连 WebSocket 后，前端立即显示正确的 mode 状态。

---

## 执行顺序

```
Phase A (后端打通):
  Task 1 → Task 2 → Task 7
  (runtime_mode 事件透传 + plan_path 携带 + 重连同步)

Phase B (前端对接):
  Task 3 → Task 4 → Task 5
  (mode 指示器 + plan 预览 + skill 菜单)

Phase C (审批闭环):
  Task 6
  (plan exit 审批流程)
```

Phase A 是纯后端，可以先做。Phase B 依赖 A 完成。Phase C 是增量优化。

---

## 前后端接口契约

### 新增 WebSocket 事件（后端 → 前端）

```json
// runtime_mode_changed - plan mode 进入/退出
{
  "type": "runtime_mode_changed",
  "run_id": "run_xxx",
  "mode": "plan" | "default",
  "topic": "refactor auth module",   // plan mode 时有值
  "plan_path": ".coding/plans/xxx-plan.md"  // plan mode 时有值
}

// plan_ready_for_review - plan 完成，等待用户审批（Task 6）
{
  "type": "plan_ready_for_review",
  "run_id": "run_xxx",
  "plan_path": ".coding/plans/xxx-plan.md",
  "summary": "计划摘要..."
}
```

### 新增 REST API（Task 6）

```
POST /api/v1/coding/{session_id}/plan/approve
  → 退出 plan mode，后续消息可执行写操作

POST /api/v1/coding/{session_id}/plan/reject
  → 退出 plan mode，不执行计划
```

### 复用已有 API

```
GET /api/v1/coding/skills          → skill 菜单数据源
GET /api/v1/coding/{session_id}/file?path={plan_path}  → plan 文件内容
```

---

## 测试策略

### 后端测试
- `test_runtime_mode_event` — `run_turn()` 在 plan mode 切换时 yield `runtime_mode_changed` 事件
- `test_plan_path_in_event` — 事件中携带正确的 `plan_path` 和 `topic`
- `test_plan_approve_api` — `POST /plan/approve` 正确退出 plan mode
- `test_plan_reject_api` — `POST /plan/reject` 正确退出 plan mode
- `test_ws_initial_mode_sync` — WebSocket 连接后立即收到当前 mode
- `test_plan_mode_write_guard` — plan mode 下 write_file 被 permission checker 拦截（已有，确认不回归）

### 前端测试
- `CodingComposer` — 输入 `/` 弹出菜单，模糊过滤，选中填充
- `codingStream` — 处理 `runtime_mode_changed` 事件更新 store
- `CodingView` — plan mode 横幅显示/隐藏
- `CodingView` — plan 文件预览弹窗

---

## Hermes Studio 参考

从 [hermes-studio](https://github.com/EKKOLearnAI/hermes-studio) README 提取的可借鉴设计：

1. **技术栈对齐** — Hermes Studio 用 Vue 3 + Naive UI + Pinia + markdown-it + highlight.js。我们当前是 Vue 3 + Pinia + markdown-it，缺 Naive UI 和 highlight.js。V5.1 暂不引入 Naive UI（保持轻量），但可以考虑加 highlight.js 做代码高亮。
2. **多 Agent 可扩展架构** — Hermes 把所有 Agent 相关代码按 `hermes/` 命名空间组织。我们已有 `components/coding/` 分目录，架构方向一致。
3. **Socket.IO 流式推送** — Hermes 用 Socket.IO，我们用原生 WebSocket。功能等价，暂不迁移。
4. **Profile 隔离** — Hermes 按 Profile 隔离配置/会话/任务。我们按 session_id 隔离，暂不需要 Profile 层。
5. **会话持久化 + 重连** — Hermes 有完整的会话恢复。我们 V5.0 已有 `CodingRuntime.resume()`，Task 7 补全 mode 同步即完成闭环。

---

## V5.2 后续展望

- Naive UI 组件库引入（approval modal、file tree、drawer）
- Monaco 编辑器替换文件预览（支持 diff editor）
- xterm.js Web 终端
- delegate 子 agent 系统（V4 tool system 强化）
- MCP 集成
