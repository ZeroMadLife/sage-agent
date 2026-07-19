import { describe, expect, it } from 'vitest'
import { serializeHarnessSurfaceContext } from './surfaceContext'

describe('serializeHarnessSurfaceContext', () => {
  it('creates the bounded backend wire contract without sharing mutable references', () => {
    const context = {
      surface: 'knowledge' as const,
      workspaceId: 'knowledge-local',
      resource: {
        type: 'knowledge_page' as const,
        id: 'page-42',
        revision: 'rev-8',
        label: 'Agent Harness',
      },
      selection: {
        type: 'graph_node' as const,
        id: 'node-42',
        revision: 'rev-8',
      },
      graphRevision: 'graph-4',
      operationRefs: [{ kind: 'knowledge_job' as const, id: 'job-7' }],
    }

    const payload = serializeHarnessSurfaceContext(context)
    context.operationRefs[0].id = 'job-changed-after-send'

    expect(payload).toEqual({
      surface: 'knowledge',
      workspace_id: 'knowledge-local',
      resource: {
        type: 'knowledge_page',
        id: 'page-42',
        revision: 'rev-8',
        label: 'Agent Harness',
      },
      selection: { type: 'graph_node', id: 'node-42', revision: 'rev-8' },
      graph_revision: 'graph-4',
      operation_refs: [{ kind: 'knowledge_job', id: 'job-7' }],
    })
  })

  it('omits an absent graph revision while preserving explicit null bindings', () => {
    expect(serializeHarnessSurfaceContext({
      surface: 'coding',
      workspaceId: 'workspace-1',
      resource: null,
      selection: null,
      operationRefs: [],
    })).toEqual({
      surface: 'coding',
      workspace_id: 'workspace-1',
      resource: null,
      selection: null,
      operation_refs: [],
    })
  })
})
