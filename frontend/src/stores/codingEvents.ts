import type {
  CodingApproval,
  CodingApprovalRequiredEvent,
  CodingServerEvent,
  CodingToolCallEvent,
  CodingToolResultEvent,
  CodingContextSnapshot,
  MemoryProposal,
} from '../types/api'
import type {
  ChatMessageViewModel,
  ExecutionActivityViewModel,
  ToolActivityViewModel,
} from '../harness/chatTypes'
import type { Ref } from 'vue'
import { parseKnowledgeRetrieval } from '../harness/knowledgeRetrieval'

export type ToolActivity = ToolActivityViewModel
export type ExecutionActivity = ExecutionActivityViewModel
export type ChatMessage = ChatMessageViewModel

export type PlanReviewState = {
  review_id: string
  plan_path: string
  summary: string
}

export type DiffInfo = {
  run_id: string
  changed_files: string[]
  file_count: number
  truncated: boolean
}

export type CodingEventState = {
  sessionId: Ref<string>
  messages: Ref<ChatMessage[]>
  isThinking: Ref<boolean>
  errorMessage: Ref<string>
  contextChars: Ref<number>
  contextSnapshot: Ref<CodingContextSnapshot | null>
  compactionState: Ref<'idle' | 'running' | 'succeeded' | 'failed'>
  compactionError: Ref<string>
  pendingApproval: Ref<CodingApproval | null>
  thinkingPhase: Ref<string>
  runtimeMode: Ref<string>
  planTopic: Ref<string>
  planPath: Ref<string>
  planReview: Ref<PlanReviewState | null>
  lastDiffInfo: Ref<DiffInfo | null>
  diffInfoByRun: Ref<Record<string, DiffInfo>>
  memoryProposals: Ref<MemoryProposal[]>
  memoryProposalRefresh: Ref<number>
  knowledgeSourceProposalRefresh: Ref<number>
}

export type CodingEventEffect = {
  approvalRequired?: boolean
  terminal?: boolean
  toolResult?: CodingToolResultEvent
  memoryProposalReady?: boolean
  knowledgeSourceProposalReady?: boolean
}

function contextReasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    insufficient_history: '历史内容不足，暂不需要压缩',
    compaction_busy: '已有压缩任务正在进行',
    context_emergency: '上下文已达到安全上限',
    invalid_previous_checkpoint: '历史压缩检查点无效',
  }
  return labels[reason] || '上下文压缩未完成'
}

export function applyCodingEvent(
  state: CodingEventState,
  event: CodingServerEvent,
): CodingEventEffect {
  if (event.type === 'turn_started') {
    state.thinkingPhase.value = '思考中'
    appendExecutionActivity(state.messages.value, {
      kind: 'model',
      label: '开始处理请求',
      status: 'running',
    })
    return {}
  }
  if (event.type === 'turn_finished') {
    state.thinkingPhase.value = ''
    settleAllExecutionActivities(state.messages.value)
    return {}
  }
  if (event.type === 'runtime_mode_changed') {
    state.runtimeMode.value = event.mode
    state.planTopic.value = event.topic || ''
    state.planPath.value = event.plan_path || ''
    if (event.mode === 'default') {
      state.planReview.value = null
    }
    return {}
  }
  if (event.type === 'plan_ready_for_review') {
    state.planReview.value = {
      review_id: event.review_id,
      plan_path: event.plan_path,
      summary: event.summary,
    }
    return {}
  }
  if (event.type === 'workspace_diff_ready') {
    const diffInfo = {
      run_id: event.run_id || '',
      changed_files: event.changed_files || [],
      file_count: event.file_count || 0,
      truncated: event.truncated || false,
    }
    state.lastDiffInfo.value = diffInfo
    if (diffInfo.run_id) {
      state.diffInfoByRun.value = {
        ...state.diffInfoByRun.value,
        [diffInfo.run_id]: diffInfo,
      }
    }
    return {}
  }
  if (event.type === 'memory_proposal_ready') {
    if (event.session_id !== state.sessionId.value) return {}
    const existing = state.memoryProposals.value.find((item) => item.proposal_id === event.proposal_id)
    if (existing) {
      existing.candidate_count = event.candidate_count
      existing.base_revision = event.base_revision
    } else {
      state.memoryProposals.value.push({
        proposal_id: event.proposal_id,
        workspace_id: '',
        status: 'pending',
        projection_status: 'pending',
        revision: 0,
        candidates: [],
        session_id: event.session_id,
        run_id: event.run_id || '',
        reflection_id: event.reflection_id,
        base_revision: event.base_revision,
        candidate_count: event.candidate_count,
        created_at: event.created_at || '',
        updated_at: event.created_at || '',
      })
    }
    state.memoryProposalRefresh.value += 1
    return { memoryProposalReady: true }
  }
  if (event.type === 'knowledge_source_proposal_created') {
    if (event.session_id !== state.sessionId.value) return {}
    state.knowledgeSourceProposalRefresh.value += 1
    return { knowledgeSourceProposalReady: true }
  }
  if (event.type === 'context_usage_updated') {
    state.contextChars.value = event.used_tokens
    const previous = state.contextSnapshot.value
    state.contextSnapshot.value = {
      configured: true,
      model_id: previous?.model_id ?? null,
      used_tokens: event.used_tokens,
      model_limit_tokens: event.model_limit_tokens,
      output_reserve_tokens: event.output_reserve_tokens,
      effective_limit_tokens: event.effective_limit_tokens,
      usage_ratio: event.usage_ratio,
      level: event.level,
      estimated: event.estimated,
      compactable: event.compactable,
      active_run_id: event.run_id ?? previous?.active_run_id ?? null,
      context_operation_active: previous?.context_operation_active ?? false,
      checkpoint_id: previous?.checkpoint_id ?? null,
      resume_status: previous?.resume_status ?? 'active',
      checkpoint_resume_enabled: previous?.checkpoint_resume_enabled ?? false,
      latest_attempt: previous?.latest_attempt ?? null,
      stale_started: previous?.stale_started ?? false,
    }
    return {}
  }
  if (event.type === 'context_compaction_started') {
    state.compactionState.value = 'running'
    state.compactionError.value = ''
    if (state.contextSnapshot.value) {
      state.contextSnapshot.value.context_operation_active = true
    }
    state.thinkingPhase.value = '正在压缩上下文...'
    return {}
  }
  if (event.type === 'context_compaction_completed') {
    state.compactionState.value = 'succeeded'
    if (state.contextSnapshot.value) {
      state.contextSnapshot.value.context_operation_active = false
      state.contextSnapshot.value.used_tokens = event.after_tokens
    }
    state.contextChars.value = event.after_tokens
    state.thinkingPhase.value = state.isThinking.value ? '思考中' : ''
    return {}
  }
  if (event.type === 'context_compaction_failed') {
    state.compactionState.value = 'failed'
    if (state.contextSnapshot.value) {
      state.contextSnapshot.value.context_operation_active = false
    }
    state.compactionError.value = contextReasonLabel(event.reason)
    if (!state.isThinking.value) state.thinkingPhase.value = ''
    return {}
  }
  if (event.type === 'model_requested') {
    if (event.prompt_chars && !state.contextSnapshot.value) {
      state.contextChars.value = event.prompt_chars
    }
    state.thinkingPhase.value = '正在请求模型...'
    settleExecutionActivity(state.messages.value, 'retry')
    appendExecutionActivity(state.messages.value, {
      kind: 'model',
      label: '请求模型响应',
      status: 'running',
    })
    return {}
  }
  if (event.type === 'model_parsed') {
    if (event.kind === 'retry') {
      state.thinkingPhase.value = '模型响应异常,正在重试...'
      settleExecutionActivity(state.messages.value, 'model', 'error')
    } else if (event.kind === 'tool' || event.kind === 'tools') {
      state.thinkingPhase.value = '正在执行工具...'
      settleExecutionActivity(state.messages.value, 'model')
    }
    return {}
  }
  if (event.type === 'retry') {
    state.thinkingPhase.value = '正在重试...'
    appendExecutionActivity(state.messages.value, {
      kind: 'retry',
      label: '正在调整执行方式',
      status: 'running',
    })
    return {}
  }
  if (event.type === 'text_delta') {
    const last = state.messages.value[state.messages.value.length - 1]
    if (last && last.isThinking) {
      last.content += event.delta
    } else {
      state.messages.value.push({
        role: 'assistant',
        content: event.delta,
        isThinking: true,
      })
    }
    return {}
  }
  if (event.type === 'tool_call') {
    appendToolActivity(state.messages.value, event)
    appendExecutionActivity(state.messages.value, {
      kind: 'tool',
      label: toolActionLabel(event.tool, event.args),
      status: 'running',
    })
    return {}
  }
  if (event.type === 'approval_required') {
    state.pendingApproval.value = approvalFromEvent(state.sessionId.value, event)
    appendToolActivity(state.messages.value, event, `等待确认: ${event.description}`)
    appendExecutionActivity(state.messages.value, {
      kind: 'approval',
      label: `等待确认 ${event.tool}`,
      detail: event.description,
      status: 'running',
    })
    state.thinkingPhase.value = '等待工具确认...'
    return { approvalRequired: true }
  }
  if (event.type === 'approval_granted') {
    if (samePendingApproval(state.pendingApproval.value, event.run_id, event.tool)) {
      state.pendingApproval.value = null
    }
    const target = findRunningTool(state.messages.value, event.tool)
    if (target) target.content = '已确认,正在执行...'
    state.thinkingPhase.value = '正在执行工具...'
    settleExecutionActivity(state.messages.value, 'approval')
    return {}
  }
  if (event.type === 'tool_result') {
    if (samePendingApproval(state.pendingApproval.value, event.run_id, event.tool)) {
      state.pendingApproval.value = null
    }
    updateToolActivity(state.messages.value, event)
    if (event.is_error) {
      settleExecutionActivity(state.messages.value, 'approval', 'error')
    }
    settleExecutionActivity(
      state.messages.value,
      'tool',
      event.is_error ? 'error' : 'done',
    )
    return { toolResult: event }
  }
  if (event.type === 'final' || event.type === 'step_limit' || event.type === 'cancelled') {
    finalizeCurrentMessage(state.messages.value, event.content)
    state.isThinking.value = false
    state.thinkingPhase.value = ''
    settleAllExecutionActivities(state.messages.value)
    return {} // NOT terminal -- wait for run_finished to refresh
  }
  if (event.type === 'run_finished') {
    if (state.pendingApproval.value && (
      !event.run_id || !state.pendingApproval.value.run_id ||
      state.pendingApproval.value.run_id === event.run_id
    )) {
      state.pendingApproval.value = null
    }
    return { terminal: true } // NOW refresh runs/sessions
  }
  if (event.type === 'error') {
    state.errorMessage.value = event.message
    state.isThinking.value = false
    state.thinkingPhase.value = ''
    settleAllExecutionActivities(state.messages.value, 'error')
    return { terminal: true }
  }
  return {}
}

function samePendingApproval(
  approval: CodingApproval | null,
  runId: string | undefined,
  tool: string,
): boolean {
  return Boolean(
    approval && approval.tool === tool &&
    (!runId || !approval.run_id || approval.run_id === runId),
  )
}

function approvalFromEvent(sessionId: string, event: CodingApprovalRequiredEvent): CodingApproval {
  return {
    approval_id: event.approval_id,
    session_id: sessionId,
    tool: event.tool,
    args: event.args,
    description: event.description,
    pattern_key: event.pattern_key,
    run_id: event.run_id,
  }
}

function appendToolActivity(
  messages: ChatMessage[],
  event: CodingToolCallEvent | CodingApprovalRequiredEvent,
  content = '',
) {
  const lastMessage = messages[messages.length - 1]
  if (!lastMessage || lastMessage.role !== 'assistant' || !lastMessage.isThinking) {
    messages.push({
      role: 'assistant',
      content: '',
      tools: [],
      activities: [],
      isThinking: true,
    })
  }
  const current = messages[messages.length - 1]
  current.tools = current.tools || []
  const existing = findRunningTool(messages, event.tool, event.args)
  if (existing) {
    existing.content = content
    return
  }
  current.tools.push({
    tool: event.tool,
    args: event.args,
    status: 'running',
    content,
  })
}

function updateToolActivity(messages: ChatMessage[], event: CodingToolResultEvent) {
  const target = findRunningTool(messages, event.tool, event.args)
  if (target) {
    target.status = event.is_error ? 'error' : 'done'
    target.content = event.content
    target.retrieval = event.tool === 'knowledge_search'
      ? parseKnowledgeRetrieval(event.content) ?? undefined
      : undefined
    return
  }
  appendSettledToolActivity(messages, event)
}

function appendSettledToolActivity(messages: ChatMessage[], event: CodingToolResultEvent) {
  const lastMessage = messages[messages.length - 1]
  if (!lastMessage || lastMessage.role !== 'assistant' || !lastMessage.isThinking) {
    messages.push({
      role: 'assistant',
      content: '',
      tools: [],
      activities: [],
      isThinking: true,
    })
  }
  const current = messages[messages.length - 1]
  current.tools = current.tools || []
  current.tools.push({
    tool: event.tool,
    args: event.args,
    status: event.is_error ? 'error' : 'done',
    content: event.content,
    retrieval: event.tool === 'knowledge_search'
      ? parseKnowledgeRetrieval(event.content) ?? undefined
      : undefined,
  })
}

function findRunningTool(
  messages: ChatMessage[],
  toolName: string,
  args?: Record<string, unknown>,
) {
  for (const msg of [...messages].reverse()) {
    if (!msg.tools) continue
    const target = [...msg.tools]
      .reverse()
      .find(
        (tool) =>
          tool.tool === toolName &&
          tool.status === 'running' &&
          (args === undefined || sameArgs(tool.args, args)),
      )
    if (target) return target
  }
  return undefined
}

function sameArgs(left: Record<string, unknown>, right: Record<string, unknown>) {
  return JSON.stringify(left) === JSON.stringify(right)
}

function finalizeCurrentMessage(messages: ChatMessage[], content: string) {
  const lastMessage = messages[messages.length - 1]
  if (lastMessage && lastMessage.isThinking) {
    lastMessage.content = content
    lastMessage.isThinking = false
  } else {
    messages.push({ role: 'assistant', content })
  }
}

function appendExecutionActivity(messages: ChatMessage[], activity: ExecutionActivity) {
  const current = currentAssistantMessage(messages)
  current.activities = current.activities || []
  const latest = current.activities[current.activities.length - 1]
  if (
    latest &&
    latest.kind === activity.kind &&
    latest.label === activity.label &&
    latest.status === 'running'
  ) {
    latest.detail = activity.detail || latest.detail
    return
  }
  current.activities.push(activity)
}

function settleExecutionActivity(
  messages: ChatMessage[],
  kind: ExecutionActivity['kind'],
  status: ExecutionActivity['status'] = 'done',
) {
  for (const message of [...messages].reverse()) {
    const activity = [...(message.activities || [])]
      .reverse()
      .find((item) => item.kind === kind && item.status === 'running')
    if (activity) {
      activity.status = status
      return
    }
  }
}

function settleAllExecutionActivities(
  messages: ChatMessage[],
  status: ExecutionActivity['status'] = 'done',
) {
  for (const message of messages) {
    for (const activity of message.activities || []) {
      if (activity.status === 'running') activity.status = status
    }
  }
}

function currentAssistantMessage(messages: ChatMessage[]): ChatMessage {
  const last = messages[messages.length - 1]
  if (last && last.role === 'assistant' && last.isThinking) return last
  const message: ChatMessage = {
    role: 'assistant',
    content: '',
    tools: [],
    activities: [],
    isThinking: true,
  }
  messages.push(message)
  return message
}

function toolActionLabel(tool: string, args: Record<string, unknown>): string {
  const path = stringArg(args, 'path')
  if (tool === 'read_file') return `读取 ${path || '文件'}`
  if (tool === 'list_files') return `列出 ${path || '工作区'} 文件`
  if (tool === 'search') return `搜索 ${stringArg(args, 'pattern') || '工作区内容'}`
  if (tool === 'run_shell') return `执行 ${stringArg(args, 'command') || '命令'}`
  if (tool === 'write_file') return `写入 ${path || '文件'}`
  if (tool === 'patch_file') return `修改 ${path || '文件'}`
  return `调用 ${tool}`
}

function stringArg(args: Record<string, unknown>, key: string): string {
  const value = args[key]
  return typeof value === 'string' ? value.trim() : ''
}
