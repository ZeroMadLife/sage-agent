<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  Bot,
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock3,
  FileText,
  FilePenLine,
  FolderSearch,
  Layers3,
  MessageSquareText,
  PauseCircle,
  Search,
  ShieldAlert,
  ShieldCheck,
  Terminal,
  Wrench,
  XCircle,
} from 'lucide-vue-next'
import type { CodingRunAuditStep, CodingRunAuditSummary } from '../../../types/api'
import type { TimelineDetail, TimelineTurn, TimelineTool } from '../../../stores/codingTimeline'

type RunStageStatus = 'pending' | 'running' | 'waiting' | 'completed' | 'failed' | 'skipped' | 'unrecorded'

type RunStageCard = {
  id: 'context' | 'model' | 'tools' | 'approval' | 'answer'
  label: string
  detail: string
  status: RunStageStatus
}

const props = withDefaults(defineProps<{
  runId: string
  tools: TimelineTool[]
  turn?: TimelineTurn
  audit?: CodingRunAuditSummary
  active?: boolean
  pendingTool?: string
}>(), {
  audit: undefined,
  turn: undefined,
  active: false,
  pendingTool: '',
})

const expandedSteps = ref(new Set<string>())

const steps = computed<CodingRunAuditStep[]>(() => {
  if (props.audit?.steps.length) {
    const occurrences = new Map<string, number>()
    return props.audit.steps.map((step) => {
      const occurrence = occurrences.get(step.tool) ?? 0
      occurrences.set(step.tool, occurrence + 1)
      const timelineTool = props.tools.filter((tool) => tool.tool === step.tool)[occurrence]
      return presentStep(step, timelineTool)
    })
  }
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
  step.status === 'running' || step.status === 'waiting' || step.status === 'approval-blocked'
)))

const headline = computed(() => {
  if (props.pendingTool) return `等待确认 · ${props.pendingTool}`
  if (props.active && currentStep.value) {
    const prefix = currentStep.value.status === 'waiting' || currentStep.value.status === 'approval-blocked'
      ? '等待确认'
      : '正在执行'
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
const runStages = computed<RunStageCard[]>(() => {
  const turn = props.turn
  if (!turn) return []
  const terminalFailed = Boolean(turn.terminal && failedTimelineStatus(turn.terminal.status))
  const modelRequests = turn.model.filter((event) => event.type === 'model_requested').length
  const auditSteps = props.audit?.steps ?? []
  const hasPersistedExecution = Boolean(
    turn.assistant || turn.terminal || turn.tools.length || auditSteps.length || props.audit?.tool_count || props.audit?.status,
  )
  const toolNames = [...new Set(
    turn.tools.length ? turn.tools.map((tool) => tool.tool) : auditSteps.map((step) => step.tool),
  )]
  const toolCount = turn.tools.length || auditSteps.length
  const approvalCount = turn.approvals.length || props.audit?.approval_count || 0
  const contextStatus = turn.context.length
    ? detailStatus(turn.context)
    : turn.model.length || turn.tools.length || turn.terminal
      ? 'completed'
      : props.active ? 'running' : 'pending'
  const answerStatus: RunStageStatus = turn.assistant?.streaming
    ? 'running'
    : terminalFailed
      ? 'failed'
      : turn.assistant && turn.terminal
        ? 'completed'
        : props.active ? 'pending' : turn.terminal ? 'unrecorded' : 'pending'

  return [
    {
      id: 'context',
      label: '输入与上下文',
      detail: turn.context.length ? `${turn.context.length} 条上下文事件` : '本轮输入已接收',
      status: contextStatus,
    },
    {
      id: 'model',
      label: '模型决策',
      detail: modelRequests ? `${modelRequests} 轮模型请求` : hasPersistedExecution ? 'Timeline 未记录模型事件' : '本轮未触发',
      status: turn.model.length ? detailStatus(turn.model) : hasPersistedExecution ? 'unrecorded' : 'skipped',
    },
    {
      id: 'tools',
      label: '工具执行',
      detail: toolCount
        ? `${toolCount} 项 · ${toolNames.slice(0, 2).join('、')}${toolNames.length > 2 ? '…' : ''}`
        : '本轮未调用',
      status: turn.tools.length
        ? toolStatus(turn.tools)
        : auditSteps.length ? auditToolStatus(auditSteps) : 'skipped',
    },
    {
      id: 'approval',
      label: '人工确认',
      detail: approvalCount ? `${approvalCount} 次审批事件` : '本轮未触发',
      status: turn.approvals.length
        ? approvalStatus(turn.approvals.map((item) => item.status))
        : props.pendingTool ? 'waiting' : approvalCount ? 'completed' : 'skipped',
    },
    {
      id: 'answer',
      label: '回答完成',
      detail: turn.assistant?.streaming
        ? '正在流式输出'
        : turn.assistant
          ? '回答已写入 timeline'
          : turn.terminal ? 'Timeline 未记录回答事件' : '等待回答',
      status: answerStatus,
    },
  ]
})

function detailStatus(events: TimelineDetail[]): RunStageStatus {
  const status = events.at(-1)?.status
  if (status === 'blocked') return 'waiting'
  if (status === 'pending' || status === 'queued' || status === 'running' || status === 'retryable') return 'running'
  if (status && failedTimelineStatus(status)) return 'failed'
  return 'completed'
}

function toolStatus(tools: TimelineTool[]): RunStageStatus {
  if (tools.some((tool) => tool.status === 'blocked')) return 'waiting'
  if (tools.some((tool) => ['pending', 'queued', 'running', 'retryable'].includes(tool.status))) return 'running'
  if (tools.some((tool) => tool.is_error || failedTimelineStatus(tool.status))) return 'failed'
  return 'completed'
}

function auditToolStatus(auditSteps: CodingRunAuditStep[]): RunStageStatus {
  if (auditSteps.some((step) => step.status === 'waiting' || step.status === 'approval-blocked')) return 'waiting'
  if (auditSteps.some((step) => step.status === 'running')) return 'running'
  if (auditSteps.some((step) => step.status === 'error' || step.status === 'policy-blocked')) return 'failed'
  return 'completed'
}

function approvalStatus(statuses: TimelineTurn['approvals'][number]['status'][]): RunStageStatus {
  if (statuses.some((status) => status === 'blocked')) return 'waiting'
  if (statuses.some((status) => ['pending', 'queued', 'running', 'retryable'].includes(status))) return 'running'
  if (statuses.some(failedTimelineStatus)) return 'failed'
  return 'completed'
}

function failedTimelineStatus(status: string) {
  return status === 'error' || status === 'cancelled' || status === 'interrupted'
}

function stageIcon(stage: RunStageCard) {
  if (stage.id === 'context') return Layers3
  if (stage.id === 'model') return BrainCircuit
  if (stage.id === 'approval') return ShieldCheck
  if (stage.id === 'answer') return MessageSquareText
  return Wrench
}

function runStageStatusLabel(status: RunStageStatus) {
  if (status === 'running') return '进行中'
  if (status === 'waiting') return '等待确认'
  if (status === 'completed') return '已完成'
  if (status === 'failed') return '失败'
  if (status === 'skipped') return '未触发'
  if (status === 'unrecorded') return '未记录'
  return '等待中'
}

function fallbackStep(tool: TimelineTool): CodingRunAuditStep {
  const args = safeArguments(tool.tool, tool.args)
  const policyBlocked = Boolean(tool.policy_reason?.trim())
  const approvalBlocked = tool.status === 'blocked' && !policyBlocked
  return {
    tool: tool.tool,
    status: policyBlocked
      ? 'policy-blocked'
      : approvalBlocked
        ? 'approval-blocked'
        : tool.status === 'running'
          ? 'running'
      : tool.is_error || tool.status === 'error' ? 'error' : 'completed',
    action_summary: actionSummary(tool.tool, args),
    result_summary: policyBlocked
      ? policyReasonLabel(tool.policy_reason)
      : approvalBlocked
        ? '等待用户确认'
        : tool.is_error
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

function presentStep(step: CodingRunAuditStep, tool?: TimelineTool): CodingRunAuditStep {
  let presented = step
  if (tool && Object.keys(tool.args).length) {
    const args = safeArguments(tool.tool, tool.args)
    presented = {
      ...step,
      action_summary: actionSummary(tool.tool, args),
      arguments_preview: JSON.stringify(args, null, 2),
    }
  }
  if (tool?.policy_reason?.trim()) {
    return {
      ...presented,
      status: 'policy-blocked',
      result_summary: policyReasonLabel(tool.policy_reason),
    }
  }
  if (tool?.status === 'blocked') {
    return {
      ...presented,
      status: 'approval-blocked',
      result_summary: '等待用户确认',
    }
  }
  return presented
}

function policyReasonLabel(reason?: string) {
  const normalized = reason?.trim().toLowerCase().replaceAll('-', '_') ?? ''
  if (normalized.includes('prior_read') || normalized.includes('read_required')) return '需先读取目标文件，已请求调整'
  if (normalized.includes('path') && (normalized.includes('allow') || normalized.includes('scope'))) return '路径不在允许范围，已请求调整'
  if (normalized.includes('plan')) return '计划模式禁止执行，已请求调整'
  if (normalized.includes('approval')) return '该操作需要确认，已请求调整'
  return '策略约束未通过，已请求调整'
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
  if (tool === 'knowledge_search') return `搜索知识 ${stringArg(args, 'query') || '知识库'}`
  if (tool === 'search_web') {
    const query = stringArg(args, 'query') || '公开资料'
    const domains = stringListArg(args, 'domains')
    return `搜索网页 ${query}${domains.length ? ` · ${domains.join(', ')}` : ''}`
  }
  if (tool === 'fetch_web') return `抓取网页 ${stringArg(args, 'url') || '正文'}`
  if (tool === 'read_file') return `读取 ${path || '文件'}`
  if (tool === 'list_files') return `列出 ${path || '.'}`
  if (tool === 'search') return `搜索 ${stringArg(args, 'pattern') || path || '工作区'}`
  if (tool === 'write_file') return `写入 ${path || '文件'}`
  if (tool === 'patch_file') return `修改 ${path || '文件'}`
  if (tool === 'run_shell') return `执行 ${stringArg(args, 'command') || 'shell 命令'}`
  if (tool === 'agent') return `子任务 ${stringArg(args, 'description') || stringArg(args, 'task') || '执行'}`
  if (tool === 'task') {
    const profile = stringArg(args, 'subagent_type')
    const description = stringArg(args, 'description')
    const label = profile ? `${profile[0].toUpperCase()}${profile.slice(1)} 子代理` : '子代理'
    return `${label}${description ? ` · ${description}` : ''}`
  }
  return `调用 ${tool}`
}

function stringArg(args: Record<string, unknown>, key: string) {
  const value = args[key]
  return typeof value === 'string' ? value.trim() : ''
}

function stringListArg(args: Record<string, unknown>, key: string) {
  const value = args[key]
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())).map(item => item.trim())
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
  if (status === 'waiting' || status === 'approval-blocked') return '等待确认'
  if (status === 'policy-blocked') return '策略已阻断'
  if (status === 'error') return '失败'
  return '已完成'
}

function statusIcon(status: string) {
  if (status === 'error') return XCircle
  if (status === 'policy-blocked') return ShieldAlert
  if (status === 'waiting' || status === 'approval-blocked') return PauseCircle
  if (status === 'completed') return CheckCircle2
  return Circle
}

function toolIcon(tool: string) {
  if (tool === 'run_shell') return Terminal
  if (tool === 'read_file') return FileText
  if (tool === 'list_files' || tool === 'search') return FolderSearch
  if (tool === 'tool_search') return Search
  if (tool === 'write_file' || tool === 'patch_file') return FilePenLine
  if (tool === 'agent' || tool === 'task') return Bot
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
  if (step.tool === 'task') return actionSummary(step.tool, args)
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
      <span class="trace-summary-row">
        <span class="trace-icon" :class="{ active }">
          <Wrench :size="14" />
        </span>
        <span class="trace-headline" :title="headline">{{ headline }}</span>
        <span v-if="durationLabel" class="trace-duration"><Clock3 :size="12" />{{ durationLabel }}</span>
        <span v-if="changedFiles.length" class="trace-files"><FilePenLine :size="12" />{{ changedFiles.length }}</span>
        <ChevronDown class="trace-chevron" :size="15" />
      </span>
      <span v-if="runStages.length" class="run-stage-flow" aria-label="本轮执行阶段">
        <span v-for="(stage, index) in runStages" :key="stage.id" class="run-stage-card" :class="stage.status" :data-stage="stage.id">
          <span class="run-stage-card__top">
            <span class="run-stage-index">{{ String(index + 1).padStart(2, '0') }}</span>
            <component :is="stageIcon(stage)" :size="14" />
            <span class="run-stage-status">{{ runStageStatusLabel(stage.status) }}</span>
          </span>
          <strong>{{ stage.label }}</strong>
          <small :title="stage.detail">{{ stage.detail }}</small>
        </span>
      </span>
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
.run-trace summary { display:grid; gap:8px; min-height:40px; padding:8px 10px 10px; color:var(--sage-text-secondary); cursor:pointer; list-style:none; font-size:var(--sage-font-xs); }
.run-trace summary::-webkit-details-marker { display:none; }
.run-trace summary:hover { background:var(--sage-surface-muted); }
.trace-summary-row { display:grid; grid-template-columns:24px minmax(0,1fr) auto auto 18px; align-items:center; gap:8px; min-height:24px; }
.trace-icon { display:grid; place-items:center; width:22px; height:22px; border-radius:var(--sage-radius-sm); color:var(--sage-text-muted); background:var(--sage-surface-muted); }
.trace-icon.active { color:var(--sage-warning); }
.trace-headline { min-width:0; overflow:hidden; color:var(--sage-text-secondary); font-weight:650; text-overflow:ellipsis; white-space:nowrap; }
.trace-duration,.trace-files { display:inline-flex; align-items:center; gap:4px; color:var(--sage-text-muted); font-family:var(--sage-font-mono); font-size:var(--sage-font-xs); white-space:nowrap; }
.trace-chevron { color:var(--sage-text-muted); transition:transform .16s ease; }
.run-trace[open] .trace-chevron { transform:rotate(180deg); }
.run-stage-flow { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:6px; min-width:0; }
.run-stage-card { display:grid; grid-template-rows:auto auto minmax(28px,auto); gap:4px; min-width:0; min-height:76px; padding:8px; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); background:var(--sage-surface-raised); }
.run-stage-card__top { display:grid; grid-template-columns:auto 14px minmax(0,1fr); align-items:center; gap:5px; color:var(--sage-text-muted); }
.run-stage-index { font-family:var(--sage-font-mono); font-size:10px; }
.run-stage-status { overflow:hidden; text-align:right; text-overflow:ellipsis; white-space:nowrap; font-size:10px; }
.run-stage-card strong { overflow:hidden; color:var(--sage-text-secondary); font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.run-stage-card small { display:-webkit-box; overflow:hidden; color:var(--sage-text-muted); font-size:10px; line-height:1.4; -webkit-box-orient:vertical; -webkit-line-clamp:2; }
.run-stage-card.completed { border-color:color-mix(in srgb,var(--sage-success) 34%,var(--sage-border)); background:color-mix(in srgb,var(--sage-success-bg) 42%,var(--sage-surface-raised)); }
.run-stage-card.completed .run-stage-card__top { color:var(--sage-success); }
.run-stage-card.running,.run-stage-card.waiting { border-color:color-mix(in srgb,var(--sage-warning) 45%,var(--sage-border)); background:color-mix(in srgb,var(--sage-warning-bg) 48%,var(--sage-surface-raised)); }
.run-stage-card.running .run-stage-card__top,.run-stage-card.waiting .run-stage-card__top { color:var(--sage-warning); }
.run-stage-card.failed { border-color:color-mix(in srgb,var(--sage-danger) 42%,var(--sage-border)); background:color-mix(in srgb,var(--sage-danger-bg) 45%,var(--sage-surface-raised)); }
.run-stage-card.failed .run-stage-card__top { color:var(--sage-danger); }
.run-stage-card.skipped,.run-stage-card.unrecorded,.run-stage-card.pending { background:var(--sage-surface-muted); }
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
.trace-step.policy-blocked .step-header > svg:first-child,.trace-step.approval-blocked .step-header > svg:first-child { color:var(--sage-warning); }
.trace-step.policy-blocked { background:color-mix(in srgb, var(--sage-warning-bg) 28%, transparent); }
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
@media (max-width:760px) { .run-stage-flow { grid-template-columns:repeat(2,minmax(0,1fr)); }.run-stage-card:last-child { grid-column:1 / -1; }.trace-files { display:none; } }
@media (max-width:640px) { .trace-summary-row { grid-template-columns:24px minmax(0,1fr) auto 18px; }.trace-duration { display:none; }.step-header { grid-template-columns:16px auto minmax(0,1fr) auto; }.step-header time { display:none; } }
@media (prefers-reduced-motion:reduce) { .trace-chevron { transition:none; } }
</style>
