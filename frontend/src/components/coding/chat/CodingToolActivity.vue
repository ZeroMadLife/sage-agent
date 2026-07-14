<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import hljs from 'highlight.js'
import {
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clipboard,
  Terminal,
  Wrench,
  XCircle,
} from 'lucide-vue-next'
import type { ToolActivity } from '../../../stores/coding'

const RESULT_PREVIEW_LIMIT = 800

const props = defineProps<{
  tools: ToolActivity[]
  isThinking: boolean
}>()

const expandedTools = ref<Set<number>>(new Set(
  props.tools.map((tool, index) => tool.status === 'running' ? index : -1).filter((index) => index >= 0),
))
const touchedTools = ref<Set<number>>(new Set())
const expandedResults = ref<Set<number>>(new Set())
const copiedPanel = ref('')
let copyTimer: ReturnType<typeof setTimeout> | undefined

const doneCount = computed(() => props.tools.filter((tool) => tool.status === 'done').length)
const errorCount = computed(() => props.tools.filter((tool) => tool.status === 'error').length)
const runningCount = computed(() => props.tools.filter((tool) => tool.status === 'running').length)

watch(
  () => props.tools.map((tool) => tool.status),
  (statuses) => {
    const next = new Set(expandedTools.value)
    statuses.forEach((status, index) => {
      if (status === 'running' && !touchedTools.value.has(index)) next.add(index)
    })
    expandedTools.value = next
  },
)

onBeforeUnmount(() => {
  if (copyTimer) clearTimeout(copyTimer)
})

function toggleTool(index: number) {
  const next = new Set(expandedTools.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  expandedTools.value = next
  touchedTools.value = new Set(touchedTools.value).add(index)
}

function toggleResult(index: number) {
  const next = new Set(expandedResults.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  expandedResults.value = next
}

function iconFor(tool: ToolActivity) {
  if (tool.status === 'running') return Circle
  if (tool.status === 'error') return XCircle
  return CheckCircle2
}

function stringArg(args: Record<string, unknown>, key: string) {
  const value = args[key]
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function toolSummary(tool: ToolActivity) {
  const path = stringArg(tool.args, 'path')
  if (tool.tool === 'read_file') return `读取 ${path || '文件'}`
  if (tool.tool === 'list_files') return `列出 ${path || '.'}`
  if (tool.tool === 'search') {
    const pattern = stringArg(tool.args, 'pattern')
    return `搜索 ${pattern || '工作区'}${path ? ` · ${path}` : ''}`
  }
  if (tool.tool === 'write_file') return `写入 ${path || '文件'}`
  if (tool.tool === 'patch_file') return `修改 ${path || '文件'}`
  if (tool.tool === 'run_shell') return stringArg(tool.args, 'command') || '执行 shell 命令'
  if (tool.tool === 'agent') return stringArg(tool.args, 'task') || '启动子智能体'
  return tool.tool.replaceAll('_', ' ')
}

function statusLabel(status: ToolActivity['status']) {
  if (status === 'running') return '执行中'
  if (status === 'error') return '失败'
  return '已完成'
}

function resultPreview(content: string, expanded: boolean) {
  if (expanded || content.length <= RESULT_PREVIEW_LIMIT) return content
  const slice = content.slice(0, RESULT_PREVIEW_LIMIT)
  const newline = slice.lastIndexOf('\n')
  return `${slice.slice(0, newline > 440 ? newline : RESULT_PREVIEW_LIMIT).trimEnd()}\n...`
}

function panelContent(content: string, kind: 'args' | 'result', expanded = true) {
  const source = kind === 'args' ? content : resultPreview(content, expanded)
  if (kind === 'args') return { language: 'json', source, html: highlight(source, 'json') }
  try {
    const parsed: unknown = JSON.parse(source)
    if (parsed !== null && typeof parsed === 'object') {
      const formatted = JSON.stringify(parsed, null, 2)
      return { language: 'json', source: formatted, html: highlight(formatted, 'json') }
    }
  } catch {
    // Non-JSON tool output remains plain text.
  }
  return { language: 'text', source, html: highlight(source, 'plaintext') }
}

function highlight(content: string, language: string) {
  return hljs.highlight(content, { language }).value
}

async function copyPanel(key: string, content: string) {
  if (!navigator.clipboard?.writeText) return
  try {
    await navigator.clipboard.writeText(content)
    copiedPanel.value = key
    if (copyTimer) clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copiedPanel.value = '' }, 1_400)
  } catch {
    copiedPanel.value = ''
  }
}
</script>

<template>
  <section class="tool-activity" aria-label="工具执行时间线">
    <div class="activity-header">
      <Wrench :size="13" />
      <span>工具执行 · {{ tools.length }} 项</span>
      <span v-if="runningCount" class="activity-status running">{{ runningCount }} 执行中</span>
      <span v-else-if="errorCount" class="activity-status error">{{ errorCount }} 失败</span>
      <span v-else class="activity-status done">{{ doneCount }} 已完成</span>
    </div>

    <div class="tool-list">
      <article v-for="(tool, index) in tools" :key="`${tool.tool}:${index}`" class="tool-item" :class="tool.status">
        <button class="tool-row" type="button" :aria-expanded="expandedTools.has(index)" @click="toggleTool(index)">
          <span class="timeline-node">
            <span v-if="tool.status === 'running'" class="tool-spinner"></span>
            <component v-else :is="iconFor(tool)" :size="14" />
          </span>
          <component :is="expandedTools.has(index) ? ChevronDown : ChevronRight" :size="14" />
          <Terminal v-if="tool.tool === 'run_shell'" :size="13" />
          <span class="tool-name">{{ tool.tool }}</span>
          <span class="tool-summary">{{ toolSummary(tool) }}</span>
          <span class="tool-status">{{ statusLabel(tool.status) }}</span>
        </button>

        <div v-if="expandedTools.has(index)" class="tool-detail">
          <section class="code-panel">
            <header class="code-panel-header">
              <span>参数 · JSON</span>
              <button type="button" title="复制参数" aria-label="复制参数" @click="copyPanel(`args:${index}`, JSON.stringify(tool.args, null, 2))">
                <Check v-if="copiedPanel === `args:${index}`" :size="13" /><Clipboard v-else :size="13" />
              </button>
            </header>
            <pre class="hljs"><code v-html="panelContent(JSON.stringify(tool.args, null, 2), 'args').html"></code></pre>
          </section>

          <section v-if="tool.content" class="code-panel result-panel" :class="{ failed: tool.status === 'error' }">
            <header class="code-panel-header">
              <span>结果 · {{ panelContent(tool.content, 'result', expandedResults.has(index)).language.toUpperCase() }}</span>
              <button type="button" title="复制结果" aria-label="复制结果" @click="copyPanel(`result:${index}`, tool.content)">
                <Check v-if="copiedPanel === `result:${index}`" :size="13" /><Clipboard v-else :size="13" />
              </button>
            </header>
            <pre class="hljs"><code v-html="panelContent(tool.content, 'result', expandedResults.has(index)).html"></code></pre>
            <button v-if="tool.content.length > RESULT_PREVIEW_LIMIT" class="show-more" type="button" @click="toggleResult(index)">
              {{ expandedResults.has(index) ? '收起输出' : '展开完整输出' }}
            </button>
          </section>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.tool-activity { width: 100%; margin: 4px 0 14px; }
.activity-header { display:flex; align-items:center; gap:6px; min-height:30px; padding-left:2px; color:var(--sage-text-muted); font-size:11px; font-weight:650; }
.activity-status { margin-left:auto; font-size:10px; font-weight:600; }.activity-status.running { color:var(--sage-warning); }.activity-status.error { color:var(--sage-danger); }.activity-status.done { color:var(--sage-success); }
.tool-list { position:relative; display:grid; gap:2px; padding-left:14px; }
.tool-list::before { position:absolute; top:17px; bottom:17px; left:7px; width:1px; background:var(--sage-border-strong); content:''; }
.tool-item { position:relative; min-width:0; }
.tool-row { display:grid; grid-template-columns:18px 14px auto auto minmax(0,1fr) auto; align-items:center; gap:6px; width:100%; min-height:34px; padding:3px 2px 3px 0; border:0; color:var(--sage-text-secondary); background:transparent; text-align:left; }
.tool-row:hover { color:var(--sage-text); }
.timeline-node { position:relative; z-index:1; display:grid; place-items:center; width:16px; height:16px; border-radius:50%; color:var(--sage-success); background:var(--sage-surface); }
.tool-item.running .timeline-node,.tool-item.running .tool-status { color:var(--sage-warning); }.tool-item.error .timeline-node,.tool-item.error .tool-status { color:var(--sage-danger); }
.tool-spinner { width:10px; height:10px; border:2px solid var(--sage-border); border-top-color:var(--sage-warning); border-radius:50%; animation:spin .65s linear infinite; }
.tool-name { font-family:var(--sage-font-mono); font-size:11px; font-weight:700; }
.tool-summary { min-width:0; overflow:hidden; color:var(--sage-text-muted); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.tool-status { color:var(--sage-success); font-size:10px; white-space:nowrap; }
.tool-detail { display:grid; gap:9px; min-width:0; margin:2px 0 10px 38px; }
.code-panel { min-width:0; overflow:hidden; border:1px solid var(--sage-border); border-radius:var(--sage-radius); background:var(--sage-code-bg); }
.code-panel.failed { border-color:color-mix(in srgb, var(--sage-danger) 38%, var(--sage-border)); }
.code-panel-header { display:flex; align-items:center; justify-content:space-between; min-height:30px; padding:0 8px 0 10px; border-bottom:1px solid var(--sage-border); color:var(--sage-code-muted); background:color-mix(in srgb, var(--sage-surface-raised) 75%, var(--sage-code-bg)); font-family:var(--sage-font-mono); font-size:10px; }
.code-panel-header button { display:grid; place-items:center; width:24px; height:24px; padding:0; border:0; border-radius:var(--sage-radius-sm); color:inherit; background:transparent; }.code-panel-header button:hover { color:var(--sage-code-text); background:rgb(255 255 255 / 7%); }
.code-panel pre { max-height:280px; margin:0; padding:11px 13px; overflow:auto; border-radius:0; background:var(--sage-code-bg); color:var(--sage-code-text); font-family:var(--sage-font-mono); font-size:11.5px; line-height:1.55; white-space:pre-wrap; word-break:break-word; }
.show-more { width:100%; min-height:28px; border:0; border-top:1px solid var(--sage-border); color:var(--sage-code-muted); background:var(--sage-code-bg); font-size:10px; }.show-more:hover { color:var(--sage-code-text); }
.code-panel :deep(.hljs-keyword),.code-panel :deep(.hljs-literal) { color:#c678dd; }.code-panel :deep(.hljs-string),.code-panel :deep(.hljs-attr) { color:#98c379; }.code-panel :deep(.hljs-number) { color:#d19a66; }
@keyframes spin { to { transform:rotate(360deg); } }
@media (max-width:640px) { .tool-row { grid-template-columns:18px 14px auto minmax(0,1fr) auto; }.tool-row > svg:nth-of-type(2) { display:none; }.tool-detail { margin-left:22px; }.tool-summary { font-size:10px; } }
@media (prefers-reduced-motion:reduce) { .tool-spinner { animation:none; } }
</style>
