import type {
  CodingApproval,
  CodingApprovalRequiredEvent,
  CodingServerEvent,
  CodingToolCallEvent,
  CodingToolResultEvent,
} from '../types/api'
import type { Ref } from 'vue'

export type ToolActivity = {
  tool: string
  args: Record<string, unknown>
  status: 'running' | 'done' | 'error'
  content: string
  durationMs?: number
}

export type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
  tools?: ToolActivity[]
  isThinking?: boolean
}

export type CodingEventState = {
  sessionId: Ref<string>
  messages: Ref<ChatMessage[]>
  isThinking: Ref<boolean>
  errorMessage: Ref<string>
  contextChars: Ref<number>
  pendingApproval: Ref<CodingApproval | null>
  thinkingPhase: Ref<string>
}

export type CodingEventEffect = {
  approvalRequired?: boolean
  terminal?: boolean
  toolResult?: CodingToolResultEvent
}

export function applyCodingEvent(
  state: CodingEventState,
  event: CodingServerEvent,
): CodingEventEffect {
  if (event.type === 'turn_started') {
    state.thinkingPhase.value = '思考中'
    return {}
  }
  if (event.type === 'turn_finished') {
    state.thinkingPhase.value = ''
    return {}
  }
  if (event.type === 'model_requested') {
    if (event.prompt_chars) state.contextChars.value = event.prompt_chars
    state.thinkingPhase.value = '正在请求模型...'
    return {}
  }
  if (event.type === 'model_parsed') {
    if (event.kind === 'retry') {
      state.thinkingPhase.value = '模型响应异常,正在重试...'
    } else if (event.kind === 'tool' || event.kind === 'tools') {
      state.thinkingPhase.value = '正在执行工具...'
    }
    return {}
  }
  if (event.type === 'retry') {
    state.thinkingPhase.value = '正在重试...'
    return {}
  }
  if (event.type === 'tool_call') {
    appendToolActivity(state.messages.value, event)
    return {}
  }
  if (event.type === 'approval_required') {
    state.pendingApproval.value = approvalFromEvent(state.sessionId.value, event)
    appendToolActivity(state.messages.value, event, `等待确认: ${event.description}`)
    state.thinkingPhase.value = '等待工具确认...'
    return { approvalRequired: true }
  }
  if (event.type === 'approval_granted') {
    const target = findRunningTool(state.messages.value, event.tool)
    if (target) target.content = '已确认,正在执行...'
    state.thinkingPhase.value = '正在执行工具...'
    return {}
  }
  if (event.type === 'tool_result') {
    updateToolActivity(state.messages.value, event)
    return { toolResult: event }
  }
  if (event.type === 'final' || event.type === 'step_limit' || event.type === 'cancelled') {
    finalizeCurrentMessage(state.messages.value, event.content)
    state.isThinking.value = false
    state.thinkingPhase.value = ''
    return { terminal: true }
  }
  if (event.type === 'error') {
    state.errorMessage.value = event.message
    state.isThinking.value = false
    state.thinkingPhase.value = ''
    return { terminal: true }
  }
  return {}
}

function approvalFromEvent(sessionId: string, event: CodingApprovalRequiredEvent): CodingApproval {
  return {
    approval_id: event.approval_id,
    session_id: sessionId,
    tool: event.tool,
    args: event.args,
    description: event.description,
    pattern_key: event.pattern_key,
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
    target.content = event.content.slice(0, 2000)
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
      isThinking: true,
    })
  }
  const current = messages[messages.length - 1]
  current.tools = current.tools || []
  current.tools.push({
    tool: event.tool,
    args: event.args,
    status: event.is_error ? 'error' : 'done',
    content: event.content.slice(0, 2000),
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
