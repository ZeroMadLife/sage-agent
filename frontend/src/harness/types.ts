export type HarnessSurfaceId = 'growth' | 'knowledge' | 'coding'

export type HarnessStageStatus = 'pending' | 'running' | 'blocked' | 'completed' | 'failed' | 'cancelled'
export type HarnessRunStatus = 'idle' | 'running' | 'blocked' | 'completed' | 'failed' | 'cancelled'

export type HarnessOperationRef = {
  kind: 'knowledge_job' | 'coding_run'
  id: string
}

export type HarnessResourceRef = {
  type: 'knowledge_page' | 'knowledge_source' | 'coding_workspace'
  id: string
  revision?: string
  label?: string
}

export type HarnessSelectionRef = {
  type: 'graph_node' | 'knowledge_page' | 'knowledge_source' | 'coding_file'
  id: string
  revision?: string
  label?: string
}

export type HarnessSurfaceContext = {
  surface: HarnessSurfaceId
  workspaceId: string
  resource: HarnessResourceRef | null
  selection: HarnessSelectionRef | null
  graphRevision?: string
  operationRefs: HarnessOperationRef[]
}

export type HarnessCapability =
  | 'chat'
  | 'retrieval'
  | 'knowledge_read'
  | 'knowledge_write'
  | 'coding_read'
  | 'coding_write'

export type HarnessSurfaceAdapter<TInput> = {
  id: HarnessSurfaceId
  definitionId: string
  capabilities: HarnessCapability[]
  buildContext: (input: TInput) => HarnessSurfaceContext
}

export type HarnessStageDefinition = {
  id: string
  label: string
  description?: string
  terminal?: boolean
}

export type HarnessTransitionDefinition = {
  id: string
  from: string
  to: string
  label?: string
}

export type HarnessDefinition = {
  id: string
  version: number
  surface: HarnessSurfaceId
  stages: HarnessStageDefinition[]
  transitions: HarnessTransitionDefinition[]
}

export type HarnessTimelineEventType =
  | 'stage_started'
  | 'stage_completed'
  | 'stage_failed'
  | 'transition_taken'
  | 'run_terminal'

export type HarnessTimelineEvent = {
  eventId: string
  runId: string
  sequence: number
  timestamp: string
  type: HarnessTimelineEventType
  definitionId: string
  definitionVersion: number
  stageId?: string
  fromStageId?: string
  toStageId?: string
  detail?: string
  status?: HarnessStageStatus
  operationRef?: HarnessOperationRef
  sourceEventId?: string
}

export type HarnessStageViewModel = HarnessStageDefinition & {
  status: HarnessStageStatus
  visitCount: number
  startedAt?: string
  completedAt?: string
  durationMs?: number
  lastSequence: number
  detail?: string
  operationRef?: HarnessOperationRef
}

export type HarnessTransitionViewModel = HarnessTransitionDefinition & {
  takenCount: number
  lastSequence: number
  active: boolean
}

export type HarnessStageEventViewModel = {
  eventId: string
  stageId: string
  label: string
  detail?: string
  status: HarnessStageStatus
  timestamp: string
  sequence: number
}

export type HarnessProjection = {
  definitionId: string
  definitionVersion: number
  definitionMissing: boolean
  runId: string
  status: HarnessRunStatus
  activeStageId: string | null
  stages: HarnessStageViewModel[]
  transitions: HarnessTransitionViewModel[]
  visitedPath: string[]
  stageEvents?: HarnessStageEventViewModel[]
  lastSequence: number
  runtimeResources?: HarnessRuntimeResource[]
}

export type HarnessRuntimeResource = {
  id: string
  kind: 'context' | 'mcp' | 'agent'
  label: string
  detail: string
  status: HarnessStageStatus
  operationRef?: HarnessOperationRef
}
