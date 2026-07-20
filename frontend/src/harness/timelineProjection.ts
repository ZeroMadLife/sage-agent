import type {
  HarnessDefinition,
  HarnessProjection,
  HarnessRunStatus,
  HarnessStageEventViewModel,
  HarnessStageStatus,
  HarnessStageViewModel,
  HarnessTimelineEvent,
  HarnessTransitionViewModel,
} from './types'

export function mergeHarnessEvents(
  current: readonly HarnessTimelineEvent[],
  incoming: readonly HarnessTimelineEvent[],
): HarnessTimelineEvent[] {
  const byId = new Map<string, HarnessTimelineEvent>()
  for (const event of current) byId.set(event.eventId, event)
  for (const event of incoming) {
    if (!byId.has(event.eventId)) byId.set(event.eventId, event)
  }
  return [...byId.values()].sort(
    (left, right) => left.sequence - right.sequence || left.eventId.localeCompare(right.eventId),
  )
}

export function projectHarnessTimeline(
  input: readonly HarnessTimelineEvent[],
  definition?: HarnessDefinition,
): HarnessProjection {
  const events = mergeHarnessEvents([], input)
  const first = events[0]
  const definitionId = first?.definitionId || definition?.id || 'unknown'
  const definitionVersion = first?.definitionVersion || definition?.version || 0
  const runId = first?.runId || ''
  const definitionMatches = Boolean(
    definition && definition.id === definitionId && definition.version === definitionVersion,
  )

  const stages = new Map<string, HarnessStageViewModel>()
  const transitions = new Map<string, HarnessTransitionViewModel>()
  const accumulatedDurations = new Map<string, number>()
  const stageEvents: HarnessStageEventViewModel[] = []
  for (const stage of definitionMatches ? definition?.stages ?? [] : []) {
    stages.set(stage.id, createStage(stage.id, stage.label, stage.description, stage.terminal))
  }
  for (const transition of definitionMatches ? definition?.transitions ?? [] : []) {
    transitions.set(transition.id, { ...transition, takenCount: 0, lastSequence: 0, active: false })
  }

  let status: HarnessRunStatus = events.length ? 'running' : 'idle'
  let activeStageId: string | null = null
  const visitedPath: string[] = []

  for (const event of events) {
    if (event.type === 'run_terminal') {
      status = terminalRunStatus(event.status)
      if (activeStageId) {
        const active = stages.get(activeStageId)
        if (active && (active.status === 'running' || active.status === 'blocked')) {
          active.status = status === 'completed' ? 'completed' : status
          active.completedAt = event.timestamp
          active.durationMs = completedDuration(active, event.timestamp, accumulatedDurations)
          active.lastSequence = event.sequence
        }
      }
      activeStageId = null
      continue
    }

    if (event.type === 'transition_taken') {
      const from = event.fromStageId || ''
      const to = event.toStageId || ''
      const transition = findTransition(transitions, from, to)
      if (transition) {
        transition.takenCount += 1
        transition.lastSequence = event.sequence
      } else if (from && to) {
        const id = `${from}:${to}`
        transitions.set(id, {
          id,
          from,
          to,
          takenCount: 1,
          lastSequence: event.sequence,
          active: false,
        })
      }
      if (to) activeStageId = to
      continue
    }

    if (!event.stageId) continue
    const stage = ensureStage(stages, event.stageId)
    stage.lastSequence = event.sequence
    if (event.detail) stage.detail = event.detail
    if (event.operationRef) stage.operationRef = event.operationRef
    stageEvents.push({
      eventId: event.eventId,
      stageId: event.stageId,
      label: stage.label,
      detail: event.detail,
      status: event.type === 'stage_failed'
        ? event.status === 'cancelled' ? 'cancelled' : 'failed'
        : event.type === 'stage_completed'
          ? 'completed'
          : event.status === 'blocked' ? 'blocked' : 'running',
      timestamp: event.timestamp,
      sequence: event.sequence,
    })

    if (event.type === 'stage_started') {
      const previousStatus = stage.status
      const newVisit = stage.status !== 'running' && stage.status !== 'blocked'
      const nextStatus = event.status === 'blocked' ? 'blocked' : 'running'
      if (newVisit) {
        stage.visitCount += 1
        stage.startedAt = nextStatus === 'running' ? event.timestamp : undefined
        stage.completedAt = undefined
        stage.durationMs = undefined
        accumulatedDurations.delete(stage.id)
        visitedPath.push(event.stageId)
      } else if (previousStatus === 'running' && nextStatus === 'blocked') {
        accumulateDuration(stage, event.timestamp, accumulatedDurations)
        stage.startedAt = undefined
      } else if (previousStatus === 'blocked' && nextStatus === 'running') {
        stage.startedAt = event.timestamp
      }
      stage.status = nextStatus
      activeStageId = event.stageId
      status = stage.status === 'blocked' ? 'blocked' : 'running'
      continue
    }

    stage.completedAt = event.timestamp
    stage.durationMs = completedDuration(stage, event.timestamp, accumulatedDurations)
    if (event.type === 'stage_failed') {
      stage.status = event.status === 'cancelled' ? 'cancelled' : 'failed'
      status = stage.status
      activeStageId = null
    } else {
      stage.status = 'completed'
      if (activeStageId === event.stageId) activeStageId = null
      if (stage.terminal) status = 'completed'
    }
  }

  for (const transition of transitions.values()) {
    transition.active = Boolean(
      activeStageId && transition.to === activeStageId && transition.lastSequence > 0,
    )
  }

  return {
    definitionId,
    definitionVersion,
    definitionMissing: !definitionMatches,
    runId,
    status,
    activeStageId,
    stages: [...stages.values()].sort((left, right) => stageOrder(definition, left.id) - stageOrder(definition, right.id) || left.lastSequence - right.lastSequence),
    transitions: [...transitions.values()].sort((left, right) => left.lastSequence - right.lastSequence),
    visitedPath,
    stageEvents,
    lastSequence: events.at(-1)?.sequence || 0,
  }
}

function createStage(id: string, label: string, description?: string, terminal?: boolean): HarnessStageViewModel {
  return {
    id,
    label,
    description,
    terminal,
    status: 'pending',
    visitCount: 0,
    lastSequence: 0,
  }
}

function ensureStage(stages: Map<string, HarnessStageViewModel>, id: string) {
  const existing = stages.get(id)
  if (existing) return existing
  const stage = createStage(id, humanize(id))
  stages.set(id, stage)
  return stage
}

function findTransition(
  transitions: Map<string, HarnessTransitionViewModel>,
  from: string,
  to: string,
) {
  return [...transitions.values()].find((item) => item.from === from && item.to === to)
}

function elapsed(start?: string, end?: string) {
  if (!start || !end) return undefined
  const duration = Date.parse(end) - Date.parse(start)
  return Number.isFinite(duration) && duration >= 0 ? duration : undefined
}

function accumulateDuration(
  stage: HarnessStageViewModel,
  end: string,
  accumulated: Map<string, number>,
) {
  const segment = elapsed(stage.startedAt, end)
  if (segment === undefined) return
  accumulated.set(stage.id, (accumulated.get(stage.id) || 0) + segment)
}

function completedDuration(
  stage: HarnessStageViewModel,
  end: string,
  accumulated: Map<string, number>,
) {
  const prior = accumulated.get(stage.id)
  const segment = elapsed(stage.startedAt, end)
  accumulated.delete(stage.id)
  if (prior === undefined && segment === undefined) return undefined
  return (prior || 0) + (segment || 0)
}

function terminalRunStatus(status?: HarnessStageStatus): 'completed' | 'failed' | 'cancelled' {
  if (status === 'failed') return 'failed'
  if (status === 'cancelled') return 'cancelled'
  return 'completed'
}

function humanize(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

function stageOrder(definition: HarnessDefinition | undefined, id: string) {
  const index = definition?.stages.findIndex((stage) => stage.id === id) ?? -1
  return index >= 0 ? index : Number.MAX_SAFE_INTEGER
}
