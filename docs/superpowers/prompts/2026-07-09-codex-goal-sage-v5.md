# Codex Goal：Sage v5 - 前端渐进式升级（Naive UI + diff 可视化 + monaco 编辑器 + xterm 终端）

## 任务类型
goal 执行（自驱完成，直到验收通过）

---

## 背景

项目代号 **Sage**，位于 `/Users/zeromadlife/Desktop/tour-agent`，分支 `dev/sage-v4`。

v4.1 已完成后端 runtime contract（typed events + ToolExecutor + 7层 system prompt + DYNAMIC_BOUNDARY + ScriptedApiClient 端到端测试）。前端也做了事件归约（codingEvents.ts）+ stream 分层（codingStream.ts）。

但前端 UI 太朴素（手写 CSS，无 UI 库），缺少 coding workbench 的核心能力。参照 **hermes-studio**（`https://github.com/EKKOLearnAI/hermes-studio`，Vue3 + Naive UI + Pinia + monaco + xterm 的生产级 agent 控制台），渐进式升级四个能力。

**先读 `docs/plans/10-SAGE-V4.1.md` 了解 v4.1 现状。**

## 两条红线

1. **旅游侧代码不动**：`agents/`、`mcp_servers/`、`core/verifier.py`、`evals/` 等全部不改
2. **现有测试不破**：`bash scripts/check.sh` 必须 350 passed，`cd frontend && npm run test -- --run` 必须 58 passed

## 参考源

- **hermes-studio**：`https://github.com/EKKOLearnAI/hermes-studio`（网页，用 WebFetch 读 ARCHITECTURE.md 和关键组件源码）
  - 技术栈：Vue3 + Naive UI + Pinia + Vite8 + monaco + xterm + mermaid
  - 工具展示：MessageItem.vue 里 diff 自动识别高亮 + NDrawer
  - workspace diff：run-chat/workspace-diff-tracker.ts（agent run 前后 git 快照）
  - 模型选择：ModelSelector.vue（弹窗 + provider 分组 + 搜索）
- **hermes-webui（vanilla JS 版）**：`/Users/zeromadlife/Desktop/hermes-study/hermes-webui/static/`
  - ui.js 的 buildToolCard / context 环 / 文件树
  - messages.js 的 SSE 事件处理

---

## 方向一：引入 Naive UI，替换手写 CSS

### 问题

当前前端全部手写 CSS（`.coding-view`、`.sidebar`、`.message` 等），样式朴素且维护成本高。

### 要做的事

#### 1.1 安装 Naive UI

```bash
cd frontend
npm install naive-ui
```

#### 1.2 逐个组件迁移

**不要一次性重写所有组件**。按以下顺序逐个迁移，每个迁移完跑测试确认不破：

1. **CodingView.vue** -- 用 `NLayout`（header+sider+content+aside）替换手写 grid 三栏
2. **CodingSidebar.vue** -- 用 `NMenu` / `NCollapse` / `NTag` 替换手写 skills/mcp/model 列表
3. **CodingComposer.vue** -- 用 `NInput`（textarea）+ `NSelect`（model）+ `NButton`（send/stop）替换手写输入
4. **CodingToolActivity.vue** -- 用 `NCollapse` + `NCode` 替换手写折叠
5. **CodingFileTree.vue** -- 用 `NTree` 替换手写文件树
6. **CodingGitBadge.vue** -- 用 `NTag` 替换手写 badge
7. **CodingApprovalCard.vue** -- 用 `NCard` + `NButton` 替换手写卡片

#### 1.3 主题

用 Naive UI 的 `NConfigProvider` + `darkTheme`（或自定义主题），统一配色。保持当前浅色风格为默认。

#### 1.4 保留 lucide-vue-next 图标

Naive UI 和 lucide 图标不冲突，继续用 lucide。

### 验收
- `npm run test -- --run` 全过（组件测试可能需要调整 mock）
- `npm run build` 通过
- 视觉上比 v4 更专业（Naive UI 组件替代手写 CSS）

---

## 方向二：diff 可视化 + workspace diff 追踪

### 问题

当前工具调用结果只显示纯文本，不识别 diff。agent 改文件后没有 workspace diff 追踪。

### 要做的事

#### 2.1 工具结果 diff 高亮

改造 `CodingToolActivity.vue`：
- 工具 result 内容如果包含 `+` / `-` / `@@` 行（unified diff 格式），自动识别并用 diff 语法高亮
- 用 Naive UI 的 `NCode` 或 highlight.js 渲染
- diff 结果超过 1000 字符截断 + "查看完整 diff" 按钮

#### 2.2 后端 workspace diff 追踪

新增 `core/coding/workspace_diff.py`：

```python
class WorkspaceDiffTracker:
    """Track file changes across agent runs via git snapshots."""

    def snapshot_before_run(self, workspace_root: Path) -> dict:
        """Take git status snapshot before a run."""
        # git status --porcelain=v1 -z

    def snapshot_after_run(self, workspace_root: Path, before: dict) -> list[FileChange]:
        """Compare before/after, return changed files with patches."""
        # git diff for each changed file

    def get_diff(self, file_path: str) -> str:
        """Return unified diff for one file."""
        # git diff <file>
```

在 `CodingRuntime.run_turn()` 里：
- run 开始前调 `snapshot_before_run()`
- run 结束后调 `snapshot_after_run()` -> yield `workspace_diff` 事件

新增 WebSocket 事件类型：
```python
{"type": "workspace_diff", "changed_files": [...], "diffs": {"path": "diff content"}}
```

#### 2.3 前端 diff drawer

新增 `frontend/src/components/CodingDiffDrawer.vue`：
- 用 Naive UI `NDrawer` + `NDrawerContent`
- 收到 `workspace_diff` 事件后，显示"本次运行修改了 N 个文件"
- 点击文件名 -> 展开该文件的 unified diff（高亮）
- 支持 diff/edit 模式切换（edit 模式用 monaco，方向三做）

### 验收
- 工具结果中的 diff 内容自动高亮
- agent 修改文件后，前端显示 workspace diff drawer
- 新增后端测试：workspace diff tracker
- 新增前端测试：diff drawer 渲染

---

## 方向三：monaco 代码编辑器

### 问题

当前文件预览只读（`<pre>` 标签），无语法高亮，无编辑能力。

### 要做的事

#### 3.1 安装 monaco

```bash
cd frontend
npm install monaco-editor
```

#### 3.2 Vite 配置

在 `vite.config.ts` 里加 monaco 的 manualChunks（避免首屏加载过大）：
```typescript
build: {
  rollupOptions: {
    output: {
      manualChunks: {
        monaco: ['monaco-editor'],
      }
    }
  }
}
```

#### 3.3 新增 CodingCodeEditor.vue

替换 `CodingFileTree.vue` 里的 `<pre>` 预览：

```vue
<script setup lang="ts">
import * as monaco from 'monaco-editor'
import { onMounted, ref, watch } from 'vue'

const props = defineProps<{
  path: string
  content: string
  readOnly?: boolean
}>()

const containerRef = ref<HTMLElement>()
let editor: monaco.editor.ICodeEditor | null = null

onMounted(() => {
  if (containerRef.value) {
    editor = monaco.editor.create(containerRef.value, {
      value: props.content,
      language: detectLanguage(props.path),
      theme: 'vs',
      readOnly: props.readOnly ?? true,
      automaticLayout: true,
      minimap: { enabled: false },
    })
  }
})

watch(() => props.content, (newContent) => {
  editor?.setValue(newContent)
})
</script>
```

#### 3.4 文件预览用 monaco

`CodingFileTree.vue` 的预览区从 `<pre>` 改为 `<CodingCodeEditor :path="..." :content="..." read-only />`

#### 3.5 diff 预览用 monaco DiffEditor

`CodingDiffDrawer.vue` 里用 monaco 的 `createDiffEditor` 展示 diff：
```typescript
monaco.editor.createDiffEditor(container, {
  original: originalModel,
  modified: modifiedModel,
})
```

### 验收
- 文件预览有语法高亮
- diff drawer 用 monaco DiffEditor 展示
- `npm run build` 通过（monaco 分包正确）

---

## 方向四：xterm 内嵌终端

### 问题

当前用户不能在网页里跑命令，只能通过 agent 的 run_shell 工具间接执行。

### 要做的事

#### 4.1 安装 xterm

```bash
cd frontend
npm install @xterm/xterm @xterm/addon-fit
```

#### 4.2 后端 WebSocket 终端端点

新增 `api/coding.py` 里加 WebSocket 终端：

```python
@router.websocket("/api/v1/coding/{session_id}/terminal")
async def coding_terminal(websocket: WebSocket, session_id: str):
    """PTY-like terminal over WebSocket."""
    await websocket.accept()
    runtime = sessions.get(session_id)
    if runtime is None:
        await websocket.close()
        return
    # 用 subprocess 跑 shell，WebSocket 双向通信
    # stdin -> subprocess, stdout/stderr -> WebSocket
```

**注意**：这不是真正的 PTY（macOS 需要 pty 模块），第一版用 subprocess + pipe 简化实现。后续可升级到 `ptyprocess`。

#### 4.3 新增 CodingTerminal.vue

```vue
<script setup lang="ts">
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { onMounted, onBeforeUnmount, ref } from 'vue'
import '@xterm/xterm/css/xterm.css'

const props = defineProps<{ sessionId: string }>()
const containerRef = ref<HTMLElement>()
let term: Terminal | null = null
let ws: WebSocket | null = null

onMounted(() => {
  term = new Terminal({ fontSize: 13 })
  const fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(containerRef.value!)
  fitAddon.fit()

  ws = new WebSocket(`ws://.../api/v1/coding/${props.sessionId}/terminal`)
  ws.onmessage = (event) => term?.write(event.data)
  term.onData((data) => ws?.send(data))
})
</script>
```

#### 4.4 集成到右栏

`CodingView.vue` 右栏加 tab 切换：Files / Terminal。
- Files tab：现有的文件树 + monaco 预览
- Terminal tab：xterm 终端

### 验收
- 网页里能打开终端，输入命令，看到输出
- 终端和文件树可切换
- `npm run build` 通过（xterm 分包正确）

---

## 不要做的事

- 不要动旅游侧代码
- 不要改 v4.1 的后端 events/tool_executor/engine/context_manager（只新增 workspace_diff）
- 不要一次性重写所有前端组件（逐个迁移）
- 不要引入 hermes-studio 的 Koa BFF / Electron / Socket.IO（我们用 FastAPI + WebSocket）
- 不要引入 vue-flow / mermaid（暂不需要）
- 不要做 i18n（暂不需要）
- 不要改 `core/coding/` 的 engine/runtime/tools（方向二只新增 workspace_diff.py）

## 执行顺序

```
方向一  Naive UI 引入 + 逐个组件迁移    ← 先建 UI 基础
   ↓
方向三  monaco 代码编辑器              ← 替换文件预览
   ↓
方向二  diff 可视化 + workspace diff   ← 依赖 monaco 的 DiffEditor
   ↓
方向四  xterm 终端                     ← 独立模块
   ↓
验收    全量测试 + build + 前后端自测截图
```

## 完成标志

- `bash scripts/check.sh` 全绿
- `cd frontend && npm run test -- --run` 全绿
- `cd frontend && npm run build` 通过
- Naive UI 替换手写 CSS
- monaco 代码编辑器有语法高亮
- 工具结果 diff 自动高亮 + workspace diff drawer
- xterm 终端可输入命令
- commit message 标注 `sage-v5`
- `docs/plans/11-SAGE-V5.md` 记录落地
- **前后端自测：启动后端+前端，截图三栏布局 + 文件预览(高亮) + 终端 + diff drawer**

## 参考文件速查

| 要解决的问题 | 参考源 |
|---|---|
| Naive UI 组件用法 | `https://www.naiveui.com/zh-CN/os-theme` |
| hermes-studio 工具 diff 展示 | WebFetch `https://github.com/EKKOLearnAI/hermes-studio` 看 MessageItem.vue |
| hermes-studio workspace diff | WebFetch 看 `packages/server/src/services/hermes/run-chat/workspace-diff-tracker.ts` |
| hermes-studio 模型选择器 | WebFetch 看 `packages/client/src/components/layout/ModelSelector.vue` |
| monaco editor 集成 | `https://github.com/microsoft/monaco-editor` |
| xterm.js 集成 | `https://github.com/xtermjs/xterm.js` |
| 当前前端结构 | `frontend/src/views/CodingView.vue` + `frontend/src/components/Coding*.vue` |
| 当前前端 store | `frontend/src/stores/coding.ts` + `codingEvents.ts` + `codingStream.ts` |
| v4.1 后端事件 | `core/coding/events.py`（workspace_diff 事件加到这里） |
