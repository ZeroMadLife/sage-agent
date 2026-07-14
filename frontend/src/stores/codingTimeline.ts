import type {
  CodingTimelineEvent,
  CodingTimelineStatus,
} from '../types/api'

export type TimelineMessage = {
  content: string
  event_id: string
  timestamp: string
  streaming?: boolean
}

export type TimelineTool = {
  id: string
  tool: string
  args: Record<string, unknown>
  status: CodingTimelineStatus
  result: string
  is_error: boolean
  policy_reason?: string
  security_event_type?: string
}

export type TimelineApproval = {
  id: string
  approval_id: string
  tool: string
  args: Record<string, unknown>
  description: string
  pattern_key: string
  status: CodingTimelineStatus
}

export type TimelineDetail = {
  id: string
  type: string
  status: CodingTimelineStatus
  timestamp: string
  payload: Record<string, unknown>
}

export type TimelineTurn = {
  id: string
  run_id: string
  user: TimelineMessage | null
  assistant: TimelineMessage | null
  tools: TimelineTool[]
  approvals: TimelineApproval[]
  model: TimelineDetail[]
  context: TimelineDetail[]
  memory: TimelineDetail[]
  agents: TimelineDetail[]
  system: TimelineDetail[]
  terminal: CodingTimelineEvent | null
}

export type TimelineProjection = {
  events: CodingTimelineEvent[]
  turns: TimelineTurn[]
  terminals: CodingTimelineEvent[]
}

export function mergeTimelineEvents(
  current: readonly CodingTimelineEvent[],
  incoming: readonly CodingTimelineEvent[],
): CodingTimelineEvent[] {
  const byId = new Map<string, CodingTimelineEvent>()
  for (const item of current) byId.set(item.event_id, item)
  for (const item of incoming) {
    if (!byId.has(item.event_id)) byId.set(item.event_id, item)
  }
  return [...byId.values()].sort(
    (left, right) => left.sequence - right.sequence || left.event_id.localeCompare(right.event_id),
  )
}

export function createTimelineProjection(
  input: readonly CodingTimelineEvent[],
): TimelineProjection {
  const events = mergeTimelineEvents([], input)
  const turnsByRun = new Map<string, TimelineTurn>()
  for (const event of events) {
    const turn = getTurn(turnsByRun, event.run_id)
    projectEvent(turn, event)
  }
  const terminals = [...turnsByRun.values()]
    .map((turn) => turn.terminal)
    .filter((event): event is CodingTimelineEvent => event !== null)
  return { events, turns: [...turnsByRun.values()], terminals }
}

function getTurn(turns: Map<string, TimelineTurn>, runId: string): TimelineTurn {
  let turn = turns.get(runId)
  if (turn) return turn
  turn = {
    id: `turn:${runId}`,
    run_id: runId,
    user: null,
    assistant: null,
    tools: [],
    approvals: [],
    model: [],
    context: [],
    memory: [],
    agents: [],
    system: [],
    terminal: null,
  }
  turns.set(runId, turn)
  return turn
}

function projectEvent(turn: TimelineTurn, event: CodingTimelineEvent): void {
  const payload = event.payload
  const type = typeof payload.type === 'string' ? payload.type : event.kind
  if (type === 'step_limit' || type === 'cancelled') {
    projectAssistant(turn, event, type)
    return
  }
  if (event.kind === 'user' || type === 'user') {
    turn.user = message(event, stringValue(payload.content))
    return
  }
  if (event.kind === 'assistant') {
    projectAssistant(turn, event, type)
    return
  }
  if (event.kind === 'tool') {
    projectTool(turn, event, type)
    return
  }
  if (event.kind === 'approval') {
    projectApproval(turn, event, type)
    return
  }
  if (event.kind === 'terminal') {
    if (!turn.terminal) turn.terminal = event
    if (turn.assistant) turn.assistant.streaming = false
    for (const model of turn.model) {
      if (isOpenStatus(model.status)) model.status = event.status
    }
    for (const tool of turn.tools) {
      if (isOpenStatus(tool.status)) {
        tool.status = event.status
        tool.is_error = event.status === 'error'
      }
    }
    for (const approval of turn.approvals) {
      if (isOpenStatus(approval.status)) approval.status = event.status
    }
    return
  }
  const detail = detailFrom(event, type)
  if (event.kind === 'model') {
    if (type === 'model_parsed') {
      const requested = [...turn.model].reverse().find(
        (item) => item.type === 'model_requested' && isOpenStatus(item.status),
      )
      if (requested) {
        requested.status = event.payload.kind === 'retry' ? 'error' : event.status
      }
    }
    turn.model.push(detail)
  }
  else if (event.kind === 'context') turn.context.push(detail)
  else if (event.kind === 'memory') turn.memory.push(detail)
  else if (event.kind === 'agent') turn.agents.push(detail)
  else turn.system.push(detail)
}

function projectAssistant(turn: TimelineTurn, event: CodingTimelineEvent, type: string): void {
  if (type === 'text_delta') {
    const delta = stringValue(event.payload.delta)
    if (!turn.assistant) turn.assistant = message(event, '', true)
    turn.assistant.content += delta
    turn.assistant.streaming = true
    return
  }
  const content = stringValue(event.payload.content)
  turn.assistant = message(event, content, false)
}

function projectTool(turn: TimelineTurn, event: CodingTimelineEvent, type: string): void {
  const toolName = stringValue(event.payload.tool)
  const args = recordValue(event.payload.args)
  if (type === 'tool_call') {
    turn.tools.push({
      id: event.event_id,
      tool: toolName,
      args,
      status: event.status,
      result: '',
      is_error: false,
    })
    return
  }
  if (type !== 'tool_result') {
    turn.system.push(detailFrom(event, type))
    return
  }
  // TODO(v7): use a backend-provided call_id; args are only an unambiguous
  // fallback while tool execution remains serial within a run.
  let target = [...turn.tools].reverse().find(
    (item) => item.tool === toolName && isOpenStatus(item.status) && equalValue(item.args, args),
  )
  target ??= [...turn.tools].reverse().find(
    (item) => item.tool === toolName && isOpenStatus(item.status),
  )
  if (!target) {
    target = {
      id: event.event_id,
      tool: toolName,
      args,
      status: event.status,
      result: '',
      is_error: false,
    }
    turn.tools.push(target)
  }
  target.result = stringValue(event.payload.content)
  target.is_error = event.payload.is_error === true
  target.status = target.is_error ? 'error' : event.status
  const policyReason = optionalString(event.payload.policy_reason)
  const securityEventType = optionalString(event.payload.security_event_type)
  if (policyReason) target.policy_reason = policyReason
  if (securityEventType) target.security_event_type = securityEventType
  settleApproval(turn, toolName, target.is_error ? 'error' : 'completed')
}

function projectApproval(turn: TimelineTurn, event: CodingTimelineEvent, type: string): void {
  if (type !== 'approval_required') {
    if (type === 'approval_granted') {
      settleApproval(turn, stringValue(event.payload.tool), event.status)
    }
    turn.system.push(detailFrom(event, type))
    return
  }
  turn.approvals.push({
    id: event.event_id,
    approval_id: stringValue(event.payload.approval_id),
    tool: stringValue(event.payload.tool),
    args: recordValue(event.payload.args),
    description: stringValue(event.payload.description),
    pattern_key: stringValue(event.payload.pattern_key),
    status: event.status,
  })
}

function settleApproval(
  turn: TimelineTurn,
  tool: string,
  status: CodingTimelineStatus,
): void {
  const approval = [...turn.approvals].reverse().find(
    (item) => item.tool === tool && (item.status === 'blocked' || item.status === 'running'),
  )
  if (approval) approval.status = status
}

function isOpenStatus(status: CodingTimelineStatus): boolean {
  return status === 'running' || status === 'blocked' || status === 'pending' || status === 'queued'
}

function equalValue(left: unknown, right: unknown): boolean {
  if (left === right) return true
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) &&
      left.length === right.length && left.every((item, index) => equalValue(item, right[index]))
  }
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false
  const leftRecord = left as Record<string, unknown>
  const rightRecord = right as Record<string, unknown>
  const leftKeys = Object.keys(leftRecord).sort()
  const rightKeys = Object.keys(rightRecord).sort()
  return leftKeys.length === rightKeys.length &&
    leftKeys.every((key, index) => key === rightKeys[index] && equalValue(leftRecord[key], rightRecord[key]))
}

function message(event: CodingTimelineEvent, content: string, streaming = false): TimelineMessage {
  return { content, event_id: event.event_id, timestamp: event.timestamp, streaming }
}

function detailFrom(event: CodingTimelineEvent, type: string): TimelineDetail {
  return {
    id: event.event_id,
    type,
    status: event.status,
    timestamp: event.timestamp,
    payload: event.payload,
  }
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}

function recordValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}
