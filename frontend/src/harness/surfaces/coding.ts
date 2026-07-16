import type { CodingTimelineEvent, CodingTimelineStatus } from '../../types/api'
import { projectHarnessTimeline } from '../timelineProjection'
import type {
  HarnessDefinition,
  HarnessRuntimeResource,
  HarnessStageStatus,
  HarnessTimelineEvent,
} from '../types'

export const codingHarnessDefinition: HarnessDefinition = {
  id: 'sage.coding.practice',
  version: 1,
  surface: 'coding',
  stages: [
    { id: 'receive', label: '接收目标' },
    { id: 'context', label: '组装上下文' },
    { id: 'plan', label: '规划' },
    { id: 'act', label: '调用工具' },
    { id: 'reply', label: '回答', terminal: true },
    { id: 'memory', label: '记忆提案' },
  ],
  transitions: [
    { id: 'receive-context', from: 'receive', to: 'context' },
    { id: 'context-plan', from: 'context', to: 'plan' },
    { id: 'plan-act', from: 'plan', to: 'act' },
    { id: 'act-plan', from: 'act', to: 'plan', label: '继续规划' },
    { id: 'plan-reply', from: 'plan', to: 'reply' },
    { id: 'reply-memory', from: 'reply', to: 'memory' },
  ],
}

export function adaptCodingTimeline(events: readonly CodingTimelineEvent[]): HarnessTimelineEvent[] {
  const output: HarnessTimelineEvent[] = []
  const modelRequestCounts = new Map<string, number>()
  const replyPreparedRuns = new Set<string>()
  const replyStartedRuns = new Set<string>()
  const ordered = [...events].sort(
    (left, right) => left.sequence - right.sequence || left.event_id.localeCompare(right.event_id),
  )
  const explicitRuns = new Set(
    ordered.filter((event) => adaptExplicitStageEvent(event)).map((event) => event.run_id),
  )
  for (const event of ordered) {
    const explicit = adaptExplicitStageEvent(event)
    if (explicit) {
      output.push(explicit)
      continue
    }
    if (explicitRuns.has(event.run_id) && event.kind !== 'terminal') continue
    const type = stringValue(event.payload.type) || stringValue(event.payload.event)
    const modelRequestCount = modelRequestCounts.get(event.run_id) || 0
    output.push(...adaptLegacyEvent(
      event,
      modelRequestCount === 0,
      !replyStartedRuns.has(event.run_id),
      replyPreparedRuns.has(event.run_id),
    ))
    if (event.kind === 'model' && type === 'model_requested') {
      modelRequestCounts.set(event.run_id, modelRequestCount + 1)
    }
    if (event.kind === 'model' && type === 'model_parsed' && event.payload.kind !== 'tool') {
      replyPreparedRuns.add(event.run_id)
    }
    if (event.kind === 'assistant' && type === 'text_delta') replyStartedRuns.add(event.run_id)
  }
  return output
}

export function projectLatestCodingHarness(
  events: readonly CodingTimelineEvent[],
  preferredRunId = '',
) {
  const ordered = [...events].sort(
    (left, right) => left.sequence - right.sequence || left.event_id.localeCompare(right.event_id),
  )
  const runId = preferredRunId || ordered.at(-1)?.run_id || ''
  const runEvents = runId ? ordered.filter((event) => event.run_id === runId) : []
  return {
    ...projectHarnessTimeline(adaptCodingTimeline(runEvents), codingHarnessDefinition),
    runtimeResources: projectCodingRuntimeResources(runEvents),
  }
}

function adaptExplicitStageEvent(event: CodingTimelineEvent): HarnessTimelineEvent | null {
  const type = stringValue(event.payload.type) || stringValue(event.payload.event)
  if (!['stage_started', 'stage_completed', 'stage_failed', 'transition_taken'].includes(type)) return null
  return eventFrom(event, type as HarnessTimelineEvent['type'], 0, {
    stageId: stringValue(event.payload.stage_id) || undefined,
    fromStageId: stringValue(event.payload.from_stage_id) || undefined,
    toStageId: stringValue(event.payload.to_stage_id) || undefined,
    detail: stringValue(event.payload.detail) || undefined,
    definitionId: stringValue(event.payload.definition_id) || codingHarnessDefinition.id,
    definitionVersion: numberValue(event.payload.definition_version) || codingHarnessDefinition.version,
    status: explicitStatus(event.status),
    operationRef: operationRef(event.payload.operation_ref),
  })
}

function adaptLegacyEvent(
  event: CodingTimelineEvent,
  initialModelRequest: boolean,
  initialAssistantDelta: boolean,
  replyPrepared: boolean,
): HarnessTimelineEvent[] {
  const type = stringValue(event.payload.type) || stringValue(event.payload.event)
  if (event.kind === 'user') {
    return [
      eventFrom(event, 'stage_started', 0, { stageId: 'receive' }),
      eventFrom(event, 'stage_completed', 1, { stageId: 'receive' }),
      transition(event, 2, 'receive', 'context'),
      eventFrom(event, 'stage_started', 3, { stageId: 'context' }),
    ]
  }
  if (event.kind === 'context') {
    return settleStageFromStatus(event, 'context', 0)
  }
  if (event.kind === 'harness' && type === 'mcp_catalog_updated') {
    return [
      eventFrom(event, 'stage_completed', 0, { stageId: 'context' }),
      transition(event, 1, 'context', 'plan'),
      eventFrom(event, 'stage_started', 2, { stageId: 'plan' }),
    ]
  }
  if (event.kind === 'model' && type === 'model_requested') {
    return [
      ...(initialModelRequest ? [
        eventFrom(event, 'stage_completed', 0, { stageId: 'context' }),
        transition(event, 1, 'context', 'plan'),
      ] : []),
      eventFrom(event, 'stage_started', 2, { stageId: 'plan' }),
    ]
  }
  if (event.kind === 'model' && type === 'model_parsed') {
    const target = event.payload.kind === 'tool' ? 'act' : 'reply'
    return [
      eventFrom(event, 'stage_completed', 0, { stageId: 'plan' }),
      transition(event, 1, 'plan', target),
    ]
  }
  if (event.kind === 'tool' && type === 'tool_call') {
    return [eventFrom(event, 'stage_started', 0, {
      stageId: 'act',
      detail: toolDetail(event),
      operationRef: { kind: 'coding_run', id: event.run_id },
    })]
  }
  if (event.kind === 'tool' && type === 'tool_result') {
    const failed = event.payload.is_error === true || event.status === 'error'
    return [
      eventFrom(event, failed ? 'stage_failed' : 'stage_completed', 0, {
        stageId: 'act',
        detail: toolDetail(event),
        status: failed ? 'failed' : 'completed',
        operationRef: { kind: 'coding_run', id: event.run_id },
      }),
      ...(failed ? [] : [transition(event, 1, 'act', 'plan')]),
    ]
  }
  if (event.kind === 'approval') {
    return [eventFrom(event, 'stage_started', 0, {
      stageId: 'act',
      status: event.status === 'blocked' ? 'blocked' : 'running',
      detail: stringValue(event.payload.description) || undefined,
    })]
  }
  if (event.kind === 'assistant' && type === 'text_delta') {
    if (!initialAssistantDelta) return []
    return [
      ...(!replyPrepared ? [
        eventFrom(event, 'stage_completed', 0, { stageId: 'plan' }),
        transition(event, 1, 'plan', 'reply'),
      ] : []),
      eventFrom(event, 'stage_started', 2, { stageId: 'reply' }),
    ]
  }
  if (event.kind === 'assistant') {
    return [eventFrom(event, 'stage_completed', 0, { stageId: 'reply' })]
  }
  if (event.kind === 'memory') {
    return settleStageFromStatus(event, 'memory', 0)
  }
  if (event.kind === 'terminal') {
    return [eventFrom(event, 'run_terminal', 0, { status: timelineStatus(event.status) })]
  }
  return []
}

function projectCodingRuntimeResources(
  events: readonly CodingTimelineEvent[],
): HarnessRuntimeResource[] {
  const resources: HarnessRuntimeResource[] = []
  const catalog = [...events].reverse().find(
    (event) => event.payload.type === 'mcp_catalog_updated',
  )
  if (catalog) {
    const servers = arrayValue(catalog.payload.servers).map(recordValue)
    const connected = servers.filter((server) => stringValue(server.status) === 'connected').length
    const configured = servers.filter((server) => (
      stringValue(server.status) === 'configured' || stringValue(server.status) === 'connected'
    )).length
    const unavailable = servers.length - configured
    const detail = [
      `${servers.length} 个服务`,
      connected ? `${connected} 已连接` : `${configured} 已配置`,
      unavailable ? `${unavailable} 未配置` : '',
    ].filter(Boolean).join(' · ')
    resources.push({
      id: 'mcp-catalog',
      kind: 'mcp',
      label: 'MCP 目录',
      detail,
      status: servers.some((server) => stringValue(server.status) === 'error')
        ? 'failed'
        : unavailable ? 'blocked' : 'completed',
    })
  }
  const context = [...events].reverse().find((event) => event.kind === 'context')
  if (context) {
    const used = numberValue(context.payload.used_tokens)
    const limit = numberValue(context.payload.effective_limit_tokens)
    resources.push({
      id: 'context-budget',
      kind: 'context',
      label: '上下文',
      detail: limit ? `${used} / ${limit} tokens` : `${used} tokens`,
      status: explicitStatus(context.status),
    })
  }
  const agentResources = new Map<string, HarnessRuntimeResource>()
  for (const event of events) {
    const eventType = stringValue(event.payload.type)
    if (event.kind !== 'agent' || (!eventType.startsWith('agent_') && !eventType.startsWith('subagent_'))) continue
    const agentId = stringValue(event.payload.child_run_id)
      || stringValue(event.payload.agent_run_id)
      || event.event_id
    const previous = agentResources.get(agentId)
    agentResources.set(agentId, {
      id: `agent:${agentId}`,
      kind: 'agent',
      label: '子代理',
      detail: stringValue(event.payload.description) || previous?.detail || agentId,
      status: explicitStatus(event.status),
    })
  }
  resources.push(...agentResources.values())
  return resources
}

function settleStageFromStatus(event: CodingTimelineEvent, stageId: string, offset: number) {
  if (event.status === 'running' || event.status === 'blocked') {
    return [eventFrom(event, 'stage_started', offset, {
      stageId,
      status: event.status === 'blocked' ? 'blocked' : 'running',
    })]
  }
  if (event.status === 'error' || event.status === 'cancelled') {
    return [eventFrom(event, 'stage_failed', offset, {
      stageId,
      status: event.status === 'cancelled' ? 'cancelled' : 'failed',
    })]
  }
  return [eventFrom(event, 'stage_completed', offset, { stageId })]
}

function transition(event: CodingTimelineEvent, offset: number, fromStageId: string, toStageId: string) {
  return eventFrom(event, 'transition_taken', offset, { fromStageId, toStageId })
}

function eventFrom(
  event: CodingTimelineEvent,
  type: HarnessTimelineEvent['type'],
  offset: number,
  overrides: Partial<HarnessTimelineEvent>,
): HarnessTimelineEvent {
  return {
    eventId: `${event.event_id}:harness:${offset}`,
    runId: event.run_id,
    sequence: event.sequence * 10 + offset,
    timestamp: event.timestamp,
    type,
    definitionId: codingHarnessDefinition.id,
    definitionVersion: codingHarnessDefinition.version,
    sourceEventId: event.event_id,
    ...overrides,
  }
}

function timelineStatus(status: CodingTimelineStatus): HarnessStageStatus {
  if (status === 'error') return 'failed'
  if (status === 'cancelled' || status === 'interrupted') return 'cancelled'
  return 'completed'
}

function explicitStatus(status: CodingTimelineStatus): HarnessStageStatus {
  if (status === 'blocked') return 'blocked'
  if (status === 'running' || status === 'queued' || status === 'pending') return 'running'
  if (status === 'error') return 'failed'
  if (status === 'cancelled' || status === 'interrupted') return 'cancelled'
  return 'completed'
}

function operationRef(value: unknown) {
  const record = recordValue(value)
  const kind = stringValue(record.kind)
  const id = stringValue(record.id)
  if ((kind === 'coding_run' || kind === 'knowledge_job') && id) return { kind, id } as const
  return undefined
}

function toolDetail(event: CodingTimelineEvent) {
  const tool = stringValue(event.payload.tool)
  const args = recordValue(event.payload.args)
  const command = stringValue(args.command)
  const path = stringValue(args.path)
  return [tool, command || path].filter(Boolean).join(' · ') || undefined
}

function stringValue(value: unknown) {
  return typeof value === 'string' ? value : ''
}

function numberValue(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}
