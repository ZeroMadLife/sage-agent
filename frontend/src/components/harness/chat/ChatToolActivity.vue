<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import hljs from 'highlight.js'
import {
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clipboard,
  FileText,
  Search,
  Terminal,
  Wrench,
  XCircle,
} from 'lucide-vue-next'
import type { ToolActivityViewModel } from '../../../harness/chatTypes'

const RESULT_PREVIEW_LIMIT = 800

const props = defineProps<{
  tools: ToolActivityViewModel[]
  isThinking: boolean
}>()

const expandedTools = ref<Set<number>>(new Set())
const expandedResults = ref<Set<number>>(new Set())
const expandedCitations = ref<Set<string>>(new Set())
const copiedPanel = ref('')
let copyTimer: ReturnType<typeof setTimeout> | undefined

const doneCount = computed(() => props.tools.filter((tool) => tool.status === 'done').length)
const errorCount = computed(() => props.tools.filter((tool) => tool.status === 'error').length)
const runningCount = computed(() => props.tools.filter((tool) => tool.status === 'running').length)

onBeforeUnmount(() => {
  if (copyTimer) clearTimeout(copyTimer)
})

function toggleTool(index: number) {
  const next = new Set(expandedTools.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  expandedTools.value = next
}

function toggleResult(index: number) {
  const next = new Set(expandedResults.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  expandedResults.value = next
}

function citationKey(index: number, citationId: string) {
  return `${index}:${citationId}`
}

function toggleCitation(index: number, citationId: string) {
  const key = citationKey(index, citationId)
  const next = new Set(expandedCitations.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedCitations.value = next
}

function iconFor(tool: ToolActivityViewModel) {
  if (tool.status === 'running') return Circle
  if (tool.status === 'error') return XCircle
  return CheckCircle2
}

function stringArg(args: Record<string, unknown>, key: string) {
  const value = args[key]
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function toolSummary(tool: ToolActivityViewModel) {
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
  if (tool.tool === 'knowledge_search') {
    if (!tool.retrieval) return `搜索知识 · ${stringArg(tool.args, 'query') || '知识库'}`
    if (tool.retrieval.status === 'unavailable') return '知识库不可用'
    if (!tool.retrieval.citations.length) return '搜索知识 · 无证据'
    return `搜索知识 · ${tool.retrieval.citations.length} 条证据`
  }
  return tool.tool.replaceAll('_', ' ')
}

function statusLabel(status: ToolActivityViewModel['status']) {
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
          <Search v-else-if="tool.tool === 'knowledge_search'" :size="13" />
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

          <section v-if="tool.retrieval" class="retrieval-panel" aria-label="知识检索证据">
            <header class="retrieval-header">
              <span>知识证据 · {{ tool.retrieval.citations.length }} 条</span>
              <span>{{ tool.retrieval.usedTokens }} / {{ tool.retrieval.tokenBudget }} tokens</span>
            </header>
            <p v-if="!tool.retrieval.citations.length" class="empty-evidence">
              {{ tool.retrieval.status === 'unavailable' ? '知识库当前不可用' : '本轮没有检索到可引用证据' }}
            </p>
            <div v-else class="citation-list">
              <article v-for="citation in tool.retrieval.citations" :key="citation.citationId" class="citation-row">
                <button
                  type="button"
                  class="citation-summary"
                  :aria-label="`${expandedCitations.has(citationKey(index, citation.citationId)) ? '收起' : '展开'}引用 ${citation.title}`"
                  :aria-expanded="expandedCitations.has(citationKey(index, citation.citationId))"
                  @click="toggleCitation(index, citation.citationId)"
                >
                  <FileText :size="14" />
                  <span class="citation-title">{{ citation.title }}</span>
                  <span class="citation-path">{{ citation.sourceRelativePath || citation.sourceKind || 'Sage Knowledge' }}</span>
                  <component :is="expandedCitations.has(citationKey(index, citation.citationId)) ? ChevronDown : ChevronRight" :size="14" />
                </button>
                <div v-if="expandedCitations.has(citationKey(index, citation.citationId))" class="citation-detail">
                  <div class="citation-meta">
                    <code>{{ citation.citationId }}</code>
                    <span>页面 {{ citation.pageRevision }}</span>
                    <span v-if="citation.sourceRevision">来源 {{ citation.sourceRevision }}</span>
                  </div>
                  <p>{{ citation.excerpt }}<span v-if="citation.truncated">...</span></p>
                </div>
              </article>
            </div>
          </section>

          <details v-if="tool.retrieval && tool.content" class="raw-result">
            <summary>查看原始检索结果</summary>
            <section class="code-panel result-panel" :class="{ failed: tool.status === 'error' }">
              <pre class="hljs"><code v-html="panelContent(tool.content, 'result', expandedResults.has(index)).html"></code></pre>
            </section>
          </details>

          <section v-else-if="tool.content" class="code-panel result-panel" :class="{ failed: tool.status === 'error' }">
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
.activity-header { display:flex; align-items:center; gap:6px; min-height:32px; padding-left:2px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); font-weight:650; }
.activity-status { margin-left:auto; font-size:var(--sage-font-xs); font-weight:600; }.activity-status.running { color:var(--sage-warning); }.activity-status.error { color:var(--sage-danger); }.activity-status.done { color:var(--sage-success); }
.tool-list { position:relative; display:grid; gap:2px; padding-left:14px; }
.tool-list::before { position:absolute; top:17px; bottom:17px; left:7px; width:1px; background:var(--sage-border-strong); content:''; }
.tool-item { position:relative; min-width:0; }
.tool-row { display:grid; grid-template-columns:18px 14px auto auto minmax(0,1fr) auto; align-items:center; gap:6px; width:100%; min-height:34px; padding:3px 2px 3px 0; border:0; color:var(--sage-text-secondary); background:transparent; text-align:left; }
.tool-row:hover { color:var(--sage-text); }
.timeline-node { position:relative; z-index:1; display:grid; place-items:center; width:16px; height:16px; border-radius:50%; color:var(--sage-success); background:var(--sage-surface); }
.tool-item.running .timeline-node,.tool-item.running .tool-status { color:var(--sage-warning); }.tool-item.error .timeline-node,.tool-item.error .tool-status { color:var(--sage-danger); }
.tool-spinner { width:10px; height:10px; border:2px solid var(--sage-border); border-top-color:var(--sage-warning); border-radius:50%; animation:spin .65s linear infinite; }
.tool-name { font-family:var(--sage-font-mono); font-size:var(--sage-font-xs); font-weight:700; }
.tool-summary { min-width:0; overflow:hidden; color:var(--sage-text-muted); font-size:var(--sage-font-xs); text-overflow:ellipsis; white-space:nowrap; }
.tool-status { color:var(--sage-success); font-size:var(--sage-font-xs); white-space:nowrap; }
.tool-detail { display:grid; gap:9px; min-width:0; margin:2px 0 10px 38px; }
.code-panel { min-width:0; overflow:hidden; border:1px solid var(--sage-border); border-radius:var(--sage-radius); background:var(--sage-code-bg); }
.code-panel.failed { border-color:color-mix(in srgb, var(--sage-danger) 38%, var(--sage-border)); }
.code-panel-header { display:flex; align-items:center; justify-content:space-between; min-height:32px; padding:0 8px 0 10px; border-bottom:1px solid var(--sage-border); color:var(--sage-code-muted); background:color-mix(in srgb, var(--sage-surface-raised) 75%, var(--sage-code-bg)); font-family:var(--sage-font-mono); font-size:var(--sage-font-xs); }
.code-panel-header button { display:grid; place-items:center; width:24px; height:24px; padding:0; border:0; border-radius:var(--sage-radius-sm); color:inherit; background:transparent; }.code-panel-header button:hover { color:var(--sage-code-text); background:rgb(255 255 255 / 7%); }
.code-panel pre { max-height:280px; margin:0; padding:11px 13px; overflow:auto; border-radius:0; background:var(--sage-code-bg); color:var(--sage-code-text); font-family:var(--sage-font-mono); font-size:11.5px; line-height:1.55; white-space:pre-wrap; word-break:break-word; }
.show-more { width:100%; min-height:30px; border:0; border-top:1px solid var(--sage-border); color:var(--sage-code-muted); background:var(--sage-code-bg); font-size:var(--sage-font-xs); }.show-more:hover { color:var(--sage-code-text); }
.code-panel :deep(.hljs-keyword),.code-panel :deep(.hljs-literal) { color:#c678dd; }.code-panel :deep(.hljs-string),.code-panel :deep(.hljs-attr) { color:#98c379; }.code-panel :deep(.hljs-number) { color:#d19a66; }
.retrieval-panel { overflow:hidden; border:1px solid var(--sage-border); border-radius:var(--sage-radius); background:var(--sage-surface); }
.retrieval-header { display:flex; align-items:center; justify-content:space-between; min-height:32px; padding:0 10px; border-bottom:1px solid var(--sage-border); color:var(--sage-text-muted); font-size:var(--sage-font-xs); font-weight:650; }
.empty-evidence { margin:0; padding:12px; color:var(--sage-text-muted); font-size:var(--sage-font-sm); }
.citation-list { display:grid; }
.citation-row + .citation-row { border-top:1px solid var(--sage-border); }
.citation-summary { display:grid; grid-template-columns:16px minmax(120px,auto) minmax(0,1fr) 16px; align-items:center; gap:8px; width:100%; min-height:38px; padding:5px 10px; border:0; color:var(--sage-text-secondary); background:transparent; text-align:left; }
.citation-summary:hover { color:var(--sage-text); background:var(--sage-surface-raised); }
.citation-title,.citation-path { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.citation-title { font-size:var(--sage-font-sm); font-weight:650; }.citation-path { color:var(--sage-text-muted); font-family:var(--sage-font-mono); font-size:var(--sage-font-xs); }
.citation-detail { padding:0 12px 12px 34px; color:var(--sage-text-secondary); font-size:var(--sage-font-sm); }.citation-detail p { margin:8px 0 0; line-height:1.65; white-space:pre-wrap; }.citation-meta { display:flex; flex-wrap:wrap; gap:8px 12px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.citation-meta code { color:var(--sage-accent); }
.raw-result { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.raw-result > summary { cursor:pointer; padding:4px 2px; }.raw-result[open] > summary { margin-bottom:7px; }
@keyframes spin { to { transform:rotate(360deg); } }
@media (max-width:640px) { .tool-row { grid-template-columns:18px 14px auto minmax(0,1fr) auto; }.tool-row > svg:nth-of-type(2) { display:none; }.tool-detail { margin-left:22px; }.tool-summary { font-size:var(--sage-font-xs); } }
@media (prefers-reduced-motion:reduce) { .tool-spinner { animation:none; } }
</style>
