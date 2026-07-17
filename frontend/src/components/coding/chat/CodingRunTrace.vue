<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock3,
  FileText,
  FilePenLine,
  FolderSearch,
  Search,
  Terminal,
  Wrench,
  XCircle,
} from 'lucide-vue-next'
import type { CodingRunAuditStep, CodingRunAuditSummary } from '../../../types/api'
import type { TimelineTool } from '../../../stores/codingTimeline'

const props = withDefaults(defineProps<{
  runId: string
  tools: TimelineTool[]
  audit?: CodingRunAuditSummary
  active?: boolean
  pendingTool?: string
}>(), {
  audit: undefined,
  active: false,
  pendingTool: '',
})

const expandedSteps = ref(new Set<string>())

const steps = computed<CodingRunAuditStep[]>(() => {
  if (props.audit?.steps.length) return props.audit.steps
  return props.tools.map((tool) => fallbackStep(tool))
})

const discoverySteps = computed(() => steps.value.filter((step) => step.tool === 'tool_search'))
const executionSteps = computed(() => steps.value.filter((step) => step.tool !== 'tool_search'))
const discoverySummary = computed(() => {
  if (discoverySteps.value.some((step) => step.status !== 'completed')) return '执行中'
  const informative = discoverySteps.value
    .map((step) => stepResultSummary(step))
    .filter((summary) => summary && summary !== '执行完成')
  return informative.at(-1) || '已完成'
})

const currentStep = computed(() => [...steps.value].reverse().find((step) => (
  step.status === 'running' || step.status === 'waiting'
)))

const headline = computed(() => {
  if (props.pendingTool) return `等待确认 · ${props.pendingTool}`
  if (props.active && currentStep.value) {
    const prefix = currentStep.value.status === 'waiting' ? '等待确认' : '正在执行'
    return `${prefix} · ${stepActionSummary(currentStep.value)}`
  }
  const status = runStatusLabel(props.active
    ? 'running'
    : props.audit?.status || (steps.value.some((step) => step.status === 'error') ? 'error' : 'completed'))
  if (executionSteps.value.length) {
    const actionPreview = executionSteps.value.slice(0, 2).map(stepActionSummary).join('、')
    const remainder = executionSteps.value.length > 2 ? ` 等 ${executionSteps.value.length} 项` : ''
    const files = changedFiles.value.length ? ` · 修改 ${changedFiles.value.length} 个文件` : ''
    return `${status} · ${actionPreview}${remainder}${files}`
  }
  if (discoverySteps.value.length) return `${status} · 工具探索 ${discoverySteps.value.length} 次`
  return props.audit?.headline || '运行过程'
})

const durationLabel = computed(() => formatDuration(props.audit?.duration_ms ?? 0))
const changedFiles = computed(() => props.audit?.changed_files ?? [])

function fallbackStep(tool: TimelineTool): CodingRunAuditStep {
  const args = safeArguments(tool.tool, tool.args)
  return {
    tool: tool.tool,
    status: tool.status === 'running' || tool.status === 'blocked'
      ? (tool.status === 'blocked' ? 'waiting' : 'running')
      : tool.is_error || tool.status === 'error' ? 'error' : 'completed',
    action_summary: actionSummary(tool.tool, args),
    result_summary: tool.is_error
      ? '执行失败'
      : tool.status === 'completed'
        ? (tool.result ? shellResultSummary(tool.result) : '执行完成')
        : tool.result
          ? shellResultSummary(tool.result)
          : '执行中',
    duration_ms: 0,
    arguments_preview: JSON.stringify(args, null, 2),
    result_preview: tool.tool === 'read_file'
      ? '已读取文件内容（摘要不展示正文）'
      : redactAndClip(tool.result),
    arguments_truncated: false,
    result_truncated: tool.result.length > 1200,
  }
}

function safeArguments(tool: string, args: Record<string, unknown>) {
  return Object.fromEntries(Object.entries(args).map(([key, value]) => {
    const normalized = key.toLowerCase().replaceAll('-', '_')
    if (/(api_?key|authorization|cookie|credential|password|secret|token)/.test(normalized)) {
      return [key, '[REDACTED]']
    }
    if (['content', 'diff', 'env', 'input', 'patch', 'text'].includes(normalized)) {
      return [key, '[OMITTED]']
    }
    if (tool === 'run_shell' && key === 'command' && typeof value === 'string') {
      return [key, redactText(value)]
    }
    return [key, value]
  }))
}

function actionSummary(tool: string, args: Record<string, unknown>) {
  const path = stringArg(args, 'path')
  if (tool === 'tool_search') return `查找工具 ${stringArg(args, 'query') || '可用能力'}`
  if (tool === 'read_file') return `读取 ${path || '文件'}`
  if (tool === 'list_files') return `列出 ${path || '.'}`
  if (tool === 'search') return `搜索 ${stringArg(args, 'pattern') || path || '工作区'}`
  if (tool === 'write_file') return `写入 ${path || '文件'}`
  if (tool === 'patch_file') return `修改 ${path || '文件'}`
  if (tool === 'run_shell') return `执行 ${stringArg(args, 'command') || 'shell 命令'}`
  if (tool === 'agent') return `子任务 ${stringArg(args, 'description') || stringArg(args, 'task') || '执行'}`
  return `调用 ${tool}`
}

function stringArg(args: Record<string, unknown>, key: string) {
  const value = args[key]
  return typeof value === 'string' ? value.trim() : ''
}

function shellResultSummary(result: string) {
  const match = result.match(/^exit_code:\s*(-?\d+)$/m)
  return match ? `退出码 ${match[1]}` : '执行完成'
}

function redactText(text: string) {
  return text
    .replace(/(authorization\s*:\s*)(?:bearer\s+|basic\s+)?[^\s'"\n]+/gi, '$1[REDACTED]')
    .replace(/\bbearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer [REDACTED]')
    .replace(/\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD))\s*=\s*[^\s]+/gi, '$1=[REDACTED]')
}

function redactAndClip(text: string) {
  const redacted = redactText(text)
  if (redacted.length <= 1200) return redacted
  return `${redacted.slice(0, 880).trimEnd()}\n… 省略 ${redacted.length - 1200} 字符 …\n${redacted.slice(-320).trimStart()}`
}

function formatDuration(milliseconds: number) {
  if (!milliseconds) return ''
  if (milliseconds < 1000) return `${milliseconds}ms`
  if (milliseconds < 60_000) return `${Math.max(1, Math.round(milliseconds / 1000))}s`
  const minutes = Math.floor(milliseconds / 60_000)
  const seconds = Math.round((milliseconds % 60_000) / 1000)
  return `${minutes}m ${seconds}s`
}

function statusLabel(status: string) {
  if (status === 'running') return '执行中'
  if (status === 'waiting') return '等待确认'
  if (status === 'error') return '失败'
  return '已完成'
}

function statusIcon(status: string) {
  if (status === 'error') return XCircle
  if (status === 'completed') return CheckCircle2
  return Circle
}

function toolIcon(tool: string) {
  if (tool === 'run_shell') return Terminal
  if (tool === 'read_file') return FileText
  if (tool === 'list_files' || tool === 'search') return FolderSearch
  if (tool === 'tool_search') return Search
  if (tool === 'write_file' || tool === 'patch_file') return FilePenLine
  if (tool === 'agent') return Bot
  return Wrench
}

function runStatusLabel(status: string) {
  if (status === 'running') return '正在运行'
  if (status === 'error') return '运行失败'
  if (status === 'cancelled') return '运行已取消'
  if (status === 'step_limit') return '达到步骤上限'
  return '运行完成'
}

function parsedArguments(step: CodingRunAuditStep) {
  if (!step.arguments_preview) return {} as Record<string, unknown>
  try {
    const parsed: unknown = JSON.parse(step.arguments_preview)
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {}
  } catch {
    return {} as Record<string, unknown>
  }
}

function stepActionSummary(step: CodingRunAuditStep) {
  const args = parsedArguments(step)
  if (step.tool === 'tool_search') {
    const query = stringArg(args, 'query')
    return `查找工具 ${query || '可用能力'}`
  }
  if (step.tool === 'agent') {
    return `子任务 ${stringArg(args, 'description') || stringArg(args, 'task') || '执行'}`
  }
  return step.action_summary
}

function stepResultSummary(step: CodingRunAuditStep) {
  if (step.tool !== 'tool_search') {
    if (step.status === 'completed' && (!step.result_summary || step.result_summary === '执行中')) {
      return '执行完成'
    }
    return step.result_summary
  }
  const preview = step.result_preview.trim()
  if (!preview) return step.result_summary
  if (/no matching deferred tools found/i.test(preview)) return '无匹配工具'
  try {
    const parsed: unknown = JSON.parse(preview)
    if (
      parsed !== null
      && typeof parsed === 'object'
      && !Array.isArray(parsed)
      && (parsed as { status?: unknown }).status === 'no_match'
    ) return '无匹配工具'
    if (!Array.isArray(parsed)) return step.result_summary
    const tools = parsed as Array<{ name?: unknown }>
    const names = tools
      .map((item) => typeof item?.name === 'string' ? item.name : '')
      .filter(Boolean)
    if (names.length) return `发现 ${names.length} 个 · ${names.slice(0, 3).join('、')}${names.length > 3 ? '…' : ''}`
  } catch {
    // Truncated or non-JSON search results keep the backend summary.
  }
  return step.result_summary
}

function commandPreview(step: CodingRunAuditStep) {
  if (step.tool !== 'run_shell' || !step.arguments_preview) return ''
  const command = stringArg(parsedArguments(step), 'command')
  return command ? redactText(command).slice(0, 220) : ''
}

function stepKey(step: CodingRunAuditStep, index: number) {
  return `${step.tool}:${index}`
}

function canExpandStep(step: CodingRunAuditStep) {
  if (!props.audit) return false
  if (step.tool === 'read_file' || step.tool === 'write_file' || step.tool === 'patch_file') return false
  return Boolean(commandPreview(step) || step.result_preview.trim())
}

function toggleStep(step: CodingRunAuditStep, index: number) {
  const key = stepKey(step, index)
  const next = new Set(expandedSteps.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedSteps.value = next
}
</script>

<template>
  <details class="run-trace" :data-run-id="runId">
    <summary>
      <span class="trace-icon" :class="{ active }">
        <Wrench :size="14" />
      </span>
      <span class="trace-headline" :title="headline">{{ headline }}</span>
      <span v-if="durationLabel" class="trace-duration"><Clock3 :size="12" />{{ durationLabel }}</span>
      <span v-if="changedFiles.length" class="trace-files"><FilePenLine :size="12" />{{ changedFiles.length }}</span>
      <ChevronDown class="trace-chevron" :size="15" />
    </summary>

    <div class="trace-content">
      <ol class="trace-steps">
        <li v-if="discoverySteps.length" class="trace-discovery">
          <Search :size="13" />
          <strong>工具探索 · {{ discoverySteps.length }} 次</strong>
          <span>{{ discoverySummary }}</span>
        </li>
        <li v-for="(step, index) in executionSteps" :key="stepKey(step, index)" class="trace-step" :class="step.status">
          <header class="step-header">
            <component :is="statusIcon(step.status)" :size="14" />
            <component :is="toolIcon(step.tool)" :size="13" />
            <strong :title="stepActionSummary(step)">{{ stepActionSummary(step) }}</strong>
            <span>{{ statusLabel(step.status) }}</span>
            <time v-if="step.duration_ms">{{ formatDuration(step.duration_ms) }}</time>
          </header>
          <div class="step-meta">
            <span v-if="stepResultSummary(step)">{{ stepResultSummary(step) }}</span>
            <button
              v-if="canExpandStep(step)"
              class="step-toggle"
              type="button"
              :aria-expanded="expandedSteps.has(stepKey(step, index))"
              @click="toggleStep(step, index)"
            >
              <ChevronDown v-if="expandedSteps.has(stepKey(step, index))" :size="12" />
              <ChevronRight v-else :size="12" />
              {{ expandedSteps.has(stepKey(step, index)) ? '收起输出' : '查看输出' }}
            </button>
          </div>
          <div v-if="expandedSteps.has(stepKey(step, index))" class="step-output">
            <code v-if="commandPreview(step)" class="step-output-command"><Terminal :size="12" />$ {{ commandPreview(step) }}</code>
            <pre v-if="step.result_preview"><code>{{ step.result_preview }}</code></pre>
            <span v-if="step.result_truncated" class="step-truncated">输出已截断</span>
          </div>
        </li>
      </ol>
      <p v-if="steps.length === 0" class="trace-empty">尚无工具步骤</p>
      <div v-if="changedFiles.length" class="changed-files">
        <strong>变更文件</strong>
        <span v-for="path in changedFiles" :key="path">{{ path }}</span>
      </div>
    </div>
  </details>
</template>

<style scoped>
.run-trace { width:100%; margin:0 0 14px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); background:var(--sage-surface); }
.run-trace summary { display:grid; grid-template-columns:24px minmax(0,1fr) auto auto 18px; align-items:center; gap:8px; min-height:40px; padding:0 10px; color:var(--sage-text-secondary); cursor:pointer; list-style:none; font-size:var(--sage-font-xs); }
.run-trace summary::-webkit-details-marker { display:none; }
.run-trace summary:hover { background:var(--sage-surface-muted); }
.trace-icon { display:grid; place-items:center; width:22px; height:22px; border-radius:var(--sage-radius-sm); color:var(--sage-text-muted); background:var(--sage-surface-muted); }
.trace-icon.active { color:var(--sage-warning); }
.trace-headline { min-width:0; overflow:hidden; color:var(--sage-text-secondary); font-weight:650; text-overflow:ellipsis; white-space:nowrap; }
.trace-duration,.trace-files { display:inline-flex; align-items:center; gap:4px; color:var(--sage-text-muted); font-family:var(--sage-font-mono); font-size:var(--sage-font-xs); white-space:nowrap; }
.trace-chevron { color:var(--sage-text-muted); transition:transform .16s ease; }
.run-trace[open] .trace-chevron { transform:rotate(180deg); }
.trace-content { border-top:1px solid var(--sage-border); }
.trace-steps { display:grid; gap:0; margin:0; padding:0; list-style:none; }
.trace-step { min-width:0; padding:9px 12px 10px; border-bottom:1px solid var(--sage-border); }
.trace-step:last-child { border-bottom:0; }
.trace-discovery { display:grid; grid-template-columns:16px minmax(0,1fr) auto; align-items:center; gap:6px; min-height:34px; padding:5px 12px; border-bottom:1px solid var(--sage-border); color:var(--sage-text-muted); font-size:var(--sage-font-xs); }
.trace-discovery strong { color:var(--sage-text-secondary); font-size:var(--sage-font-sm); }
.step-header { display:grid; grid-template-columns:16px auto minmax(0,1fr) auto auto; align-items:center; gap:6px; min-height:22px; color:var(--sage-text-muted); }
.step-header strong { min-width:0; overflow:hidden; color:var(--sage-text-secondary); font-size:var(--sage-font-sm); text-overflow:ellipsis; white-space:nowrap; }
.step-header span,.step-header time { font-size:var(--sage-font-xs); white-space:nowrap; }
.trace-step.completed .step-header > svg:first-child { color:var(--sage-success); }
.trace-step.running .step-header > svg:first-child,.trace-step.waiting .step-header > svg:first-child { color:var(--sage-warning); }
.trace-step.error .step-header > svg:first-child { color:var(--sage-danger); }
.step-meta { display:flex; align-items:center; gap:10px; min-height:22px; margin:2px 0 0 22px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }
.step-toggle { display:inline-flex; align-items:center; gap:2px; min-height:22px; margin-left:auto; padding:0 3px; border:0; color:var(--sage-text-muted); background:transparent; font-size:var(--sage-font-xs); }
.step-toggle:hover { color:var(--sage-text-secondary); }
.step-output { display:grid; gap:0; min-width:0; max-height:280px; margin:6px 0 0 22px; overflow:auto; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); background:var(--sage-code-bg); color:var(--sage-code-text); font-family:var(--sage-font-mono); }
.step-output-command { display:flex; align-items:flex-start; gap:6px; padding:9px 11px; border-bottom:1px solid var(--sage-border); overflow-wrap:anywhere; font-size:var(--sage-font-xs); line-height:1.55; }
.step-output-command svg { flex:none; margin-top:2px; }
.step-output pre { margin:0; padding:10px 11px; overflow:visible; background:transparent; color:inherit; font:inherit; font-size:var(--sage-font-xs); line-height:1.6; white-space:pre-wrap; word-break:break-word; }
.step-truncated { padding:6px 11px; border-top:1px solid var(--sage-border); color:var(--sage-code-muted); font-size:var(--sage-font-xs); }
.trace-empty { margin:0; padding:12px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }
.changed-files { display:flex; flex-wrap:wrap; gap:5px 10px; padding:9px 12px; border-top:1px solid var(--sage-border); color:var(--sage-text-muted); font-size:var(--sage-font-xs); }
.changed-files strong { color:var(--sage-text-secondary); }
.changed-files span { font-family:var(--sage-font-mono); overflow-wrap:anywhere; }
@media (max-width:640px) { .run-trace summary { grid-template-columns:24px minmax(0,1fr) auto 18px; }.trace-files { display:none; }.step-header { grid-template-columns:16px auto minmax(0,1fr) auto; }.step-header time { display:none; } }
@media (prefers-reduced-motion:reduce) { .trace-chevron { transition:none; } }
</style>
