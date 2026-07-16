import type {
  KnowledgeGraphNode,
  KnowledgeJob,
  KnowledgePage,
} from '../../types/api'
import type { HarnessSurfaceAdapter } from '../types'

export type KnowledgeSurfaceContextInput = {
  workspaceId: string
  graphRevision?: string
  selectedNode: KnowledgeGraphNode | null
  selectedPage: KnowledgePage | null
  activeJobs: KnowledgeJob[]
}

export const knowledgeSurfaceAdapter: HarnessSurfaceAdapter<KnowledgeSurfaceContextInput> = {
  id: 'knowledge',
  definitionId: 'sage.knowledge-harness',
  capabilities: ['chat', 'retrieval', 'knowledge_read', 'knowledge_write'],
  buildContext(input) {
    const node = input.selectedNode
    const page = input.selectedPage
    const resource = page
      ? {
          type: 'knowledge_page' as const,
          id: page.page_id,
          revision: page.current_revision,
          label: page.title,
        }
      : node?.source_id
        ? {
            type: 'knowledge_source' as const,
            id: node.source_id,
            revision: node.source_revision ?? undefined,
            label: node.label,
          }
        : null

    return {
      surface: 'knowledge',
      workspaceId: input.workspaceId,
      resource,
      selection: node
        ? {
            type: 'graph_node',
            id: node.node_id,
            revision: node.page_revision ?? node.source_revision ?? input.graphRevision,
            label: node.label,
          }
        : page
          ? {
              type: 'knowledge_page',
              id: page.page_id,
              revision: page.current_revision,
              label: page.title,
            }
          : null,
      graphRevision: input.graphRevision,
      operationRefs: input.activeJobs.map((job) => ({
        kind: 'knowledge_job' as const,
        id: job.job_id,
      })),
    }
  },
}
