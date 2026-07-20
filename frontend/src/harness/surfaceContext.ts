import type { HarnessSurfaceContext } from './types'

export type HarnessSurfaceContextPayload = {
  surface: HarnessSurfaceContext['surface']
  workspace_id: string
  resource: {
    type: NonNullable<HarnessSurfaceContext['resource']>['type']
    id: string
    revision?: string
    label?: string
  } | null
  selection: {
    type: NonNullable<HarnessSurfaceContext['selection']>['type']
    id: string
    revision?: string
    label?: string
  } | null
  graph_revision?: string
  operation_refs: Array<{ kind: 'knowledge_job' | 'coding_run'; id: string }>
}

export function serializeHarnessSurfaceContext(
  context: HarnessSurfaceContext,
): HarnessSurfaceContextPayload {
  return {
    surface: context.surface,
    workspace_id: context.workspaceId,
    resource: context.resource ? { ...context.resource } : null,
    selection: context.selection ? { ...context.selection } : null,
    ...(context.graphRevision ? { graph_revision: context.graphRevision } : {}),
    operation_refs: context.operationRefs.map((operation) => ({ ...operation })),
  }
}
