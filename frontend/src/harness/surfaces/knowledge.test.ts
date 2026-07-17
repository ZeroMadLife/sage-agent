import { describe, expect, it } from 'vitest'
import type { KnowledgeGraphNode, KnowledgeJob, KnowledgePage } from '../../types/api'
import { knowledgeSurfaceAdapter } from './knowledge'

const node: KnowledgeGraphNode = {
  node_id: 'node-42',
  kind: 'page',
  label: 'Agent Harness',
  page_id: 'page-42',
  page_revision: 'rev-8',
  source_id: 'source-2',
  source_revision: 'source-rev-3',
  properties: {},
}

const page: KnowledgePage = {
  page_id: 'page-42',
  path: 'harness/agent.md',
  title: 'Agent Harness',
  current_revision: 'rev-8',
  updated_at: '2026-07-16T00:00:00Z',
  revisions: [],
}

const job: KnowledgeJob = {
  job_id: 'job-7',
  workspace_id: 'workspace-1',
  source_root_id: 'root-1',
  source_kind: 'local',
  source_label: 'Sage learning',
  relative_directory: '.',
  pipeline_version: 'v1',
  status: 'running',
  cancel_requested: false,
  total_items: 2,
  processed_items: 1,
  succeeded_items: 1,
  skipped_items: 0,
  failed_items: 0,
  cancelled_items: 0,
  latest_sequence: 3,
  created_at: '',
  started_at: '',
  completed_at: null,
  updated_at: '',
  items: [],
}

describe('knowledgeSurfaceAdapter', () => {
  it('binds a selected node to its page and immutable revisions', () => {
    const context = knowledgeSurfaceAdapter.buildContext({
      workspaceId: 'workspace-1',
      graphRevision: 'graph-rev-4',
      selectedNode: node,
      selectedPage: page,
      activeJobs: [job],
    })

    expect(context).toEqual({
      surface: 'knowledge',
      workspaceId: 'workspace-1',
      resource: {
        type: 'knowledge_page',
        id: 'page-42',
        revision: 'rev-8',
        label: 'Agent Harness',
      },
      selection: {
        type: 'graph_node',
        id: 'node-42',
        revision: 'rev-8',
        label: 'Agent Harness',
      },
      graphRevision: 'graph-rev-4',
      operationRefs: [{ kind: 'knowledge_job', id: 'job-7' }],
    })
  })

  it('does not invent a resource when nothing is selected', () => {
    const context = knowledgeSurfaceAdapter.buildContext({
      workspaceId: 'workspace-1',
      selectedNode: null,
      selectedPage: null,
      activeJobs: [],
    })

    expect(context.resource).toBeNull()
    expect(context.selection).toBeNull()
    expect(context.operationRefs).toEqual([])
  })
})
