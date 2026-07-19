import { describe, expect, it } from 'vitest'
import type { KnowledgeGraphNode } from '../types/api'
import {
  buildKnowledgeNodeResearchModel,
  buildKnowledgeNodeResearchPrompt,
} from './knowledgeNodeResearch'

const node: KnowledgeGraphNode = {
  node_id: 'node-harness',
  kind: 'concept',
  label: 'Agent Harness',
  page_id: 'page-harness',
  page_revision: 'page-rev-3',
  source_id: 'source-harness',
  source_revision: 'source-rev-7',
  properties: {},
}

describe('knowledge node research', () => {
  it('builds a factual preview from current graph data without inventing retrieval receipts', () => {
    const model = buildKnowledgeNodeResearchModel({
      node,
      graphRevision: 'graph-rev-9',
      neighborhood: {
        snapshot: {
          graph_revision: 'graph-rev-9', workspace_id: 'knowledge-local', wiki_watermark: 'wm-1',
          projector_id: 'sage.graph', projector_version: '1', config_hash: 'hash', status: 'ready',
          node_count: 1, edge_count: 2, warning_count: 0, error: null, created_at: '', completed_at: '', stale: false,
        },
        center: node,
        nodes: [node],
        edges: [
          { edge_id: 'e1', source_node_id: 'node-harness', target_node_id: 'n2', kind: 'WIKILINK', directed: true, weight: 1, confidence: 1, extractor_id: 'wiki', extractor_version: '1', properties: {}, evidence: [] },
          { edge_id: 'e2', source_node_id: 'n3', target_node_id: 'node-harness', kind: 'EVIDENCED_BY', directed: true, weight: 1, confidence: 1, extractor_id: 'source', extractor_version: '1', properties: {}, evidence: [] },
        ],
      },
      alignments: [{
        capability_id: 'harness', label: '可靠 Harness', coverage: 0.6, status: 'learning',
        matched_keywords: ['harness'], missing_keywords: [], matched_node_ids: ['node-harness'],
      }],
    })

    expect(model).toEqual({
      nodeId: 'node-harness', label: 'Agent Harness', graphRevision: 'graph-rev-9',
      pageRevision: 'page-rev-3', sourceRevision: 'source-rev-7',
      directConnectionCount: 2, goalCapability: '可靠 Harness', evidenceBound: true,
    })
  })

  it('keeps Web and MCP research behind an explicit plan confirmation', () => {
    const model = buildKnowledgeNodeResearchModel({ node, graphRevision: 'graph-rev-9' })
    const prompt = buildKnowledgeNodeResearchPrompt('evidence', model)

    expect(prompt).toContain('先等待我确认计划，再使用 Web/MCP')
    expect(prompt).toContain('只创建待审批 proposal')
    expect(prompt).not.toContain('已经检索')
  })

  it('marks a graph-only node as an evidence gap', () => {
    const model = buildKnowledgeNodeResearchModel({
      node: { ...node, page_revision: null, source_revision: null },
      graphRevision: 'graph-rev-9',
    })
    const prompt = buildKnowledgeNodeResearchPrompt('understand', model)

    expect(model.evidenceBound).toBe(false)
    expect(prompt).toContain('只有 graph revision')
    expect(prompt).toContain('不要把关系推断写成事实')
  })
})
