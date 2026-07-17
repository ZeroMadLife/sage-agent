import { expect, it } from 'vitest'
import type { KnowledgeGraph, KnowledgeGraphCommunities } from '../../types/api'
import {
  communitySeedPositions,
  featuredNodeIds,
  graphFocus,
} from './knowledgeGraphPresentation'

const nodes = [
  { node_id: 'center', kind: 'page' as const, label: '中心', page_id: 'page-center', page_revision: 'rev', source_id: null, source_revision: null, properties: {} },
  { node_id: 'page-neighbor', kind: 'page' as const, label: '页面邻居', page_id: 'page-neighbor', page_revision: 'rev', source_id: null, source_revision: null, properties: {} },
  { node_id: 'source-neighbor', kind: 'source' as const, label: 'source.md', page_id: null, page_revision: null, source_id: 'source', source_revision: 'sha', properties: {} },
  { node_id: 'isolated', kind: 'concept' as const, label: '孤立概念', page_id: null, page_revision: null, source_id: null, source_revision: null, properties: {} },
]

const graph: KnowledgeGraph = {
  snapshot: {
    graph_revision: 'graph', workspace_id: 'workspace', wiki_watermark: 'wiki',
    projector_id: 'projector', projector_version: '1', config_hash: 'hash', status: 'ready',
    node_count: 4, edge_count: 2, warning_count: 0, error: null, created_at: '',
    completed_at: '', stale: false,
  },
  nodes,
  edges: [
    { edge_id: 'edge-source', source_node_id: 'center', target_node_id: 'source-neighbor', kind: 'EVIDENCED_BY', directed: true, weight: 4, confidence: 1, extractor_id: 'test', extractor_version: '1', properties: {}, evidence: [] },
    { edge_id: 'edge-page', source_node_id: 'center', target_node_id: 'page-neighbor', kind: 'WIKILINK', directed: true, weight: 1, confidence: 1, extractor_id: 'test', extractor_version: '1', properties: {}, evidence: [] },
  ],
  offset: 0,
  next_offset: null,
  has_more: false,
}

const communities: KnowledgeGraphCommunities = {
  analysis: {
    analysis_revision: 'analysis', workspace_id: 'workspace', graph_revision: 'graph',
    goal_revision: 'goal', algorithm_id: 'louvain', algorithm_version: '1', seed: 42,
    resolution: 1, threshold: 0, status: 'ready', community_count: 2, insight_count: 0,
    error: null, created_at: '', completed_at: '',
  },
  communities: [],
  node_metrics: [
    { node_id: 'center', community_id: 'community-a', degree: 2, weighted_degree: 5, bridge_score: 0.8 },
    { node_id: 'page-neighbor', community_id: 'community-a', degree: 1, weighted_degree: 1, bridge_score: 0 },
    { node_id: 'source-neighbor', community_id: 'community-a', degree: 1, weighted_degree: 4, bridge_score: 0 },
    { node_id: 'isolated', community_id: 'community-b', degree: 0, weighted_degree: 0, bridge_score: 0 },
  ],
}

it('focuses all direct relations while limiting readable labels', () => {
  const focus = graphFocus(graph, 'center', 1)

  expect([...focus.connectedNodeIds]).toEqual(['page-neighbor', 'source-neighbor'])
  expect([...focus.focusedEdgeIds]).toEqual(['edge-page', 'edge-source'])
  expect([...focus.labelNodeIds]).toEqual(['center', 'page-neighbor'])
})

it('prefers meaningful connected pages for global labels', () => {
  expect([...featuredNodeIds(graph, communities, 2)]).toEqual(['center', 'page-neighbor'])
})

it('creates deterministic community-separated seed positions', () => {
  const first = communitySeedPositions(graph, communities)
  const second = communitySeedPositions(graph, communities)

  expect(first).toEqual(second)
  expect(first.get('isolated')).not.toEqual(first.get('center'))
})
