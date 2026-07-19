import { expect, it } from 'vitest'
import type { KnowledgeGraph, KnowledgeGraphCommunities } from '../../types/api'
import {
  communitySeedPositions,
  featuredNodeIds,
  focusedGraphEdgeColor,
  graphEdgePresentation,
  graphScopeNodeIds,
  graphPerformanceProfile,
  graphFocus,
  graphInteractionPalette,
  graphLegendItems,
  shortestGraphPath,
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

it('builds a real legend for the active graph color mode', () => {
  const analyzed = {
    ...communities,
    communities: [
      { community_id: 'community-a', label: 'Harness', node_count: 3, edge_count: 2, cohesion: 0.7, properties: {} },
      { community_id: 'community-b', label: '孤立知识', node_count: 1, edge_count: 0, cohesion: 0, properties: {} },
    ],
  }
  const colors = {
    type: { page: '#00f', source: '#f90', concept: '#80f' },
    community: new Map([['community-a', '#0aa'], ['community-b', '#a0a']]),
  }

  expect(graphLegendItems(graph, analyzed, 'community', colors, 1)).toEqual([{
    id: 'community-a', label: 'Harness', count: 3, color: '#0aa',
  }])
  expect(graphLegendItems(graph, analyzed, 'type', colors, 2)).toEqual([
    { id: 'page', label: '页面', count: 2, color: '#00f' },
    { id: 'concept', label: '概念', count: 1, color: '#80f' },
  ])
})

it('defines deterministic 200, 1k and 5k rendering budgets', () => {
  expect(graphPerformanceProfile(200, 800)).toMatchObject({
    tier: 'small', maxRenderedEdges: 800, hideEdgesOnMove: false, useListFallback: false,
  })
  expect(graphPerformanceProfile(1_000, 6_000)).toMatchObject({
    tier: 'medium', maxRenderedEdges: 6_000, barnesHutOptimize: true,
  })
  expect(graphPerformanceProfile(5_000, 40_000)).toMatchObject({
    tier: 'large', maxRenderedEdges: 20_000, hideEdgesOnMove: true,
  })
  expect(graphPerformanceProfile(1_201, 2_000, true)).toMatchObject({
    tier: 'fallback', useListFallback: true,
  })
})

it('keeps the full relationship network visible before focus', () => {
  const wikiEdge = graphEdgePresentation('WIKILINK', 1, 1, false, 'small')
  const evidenceEdge = graphEdgePresentation('EVIDENCED_BY', 4, 1, false, 'small')

  expect(wikiEdge).toMatchObject({
    color: '#c8cace',
    hidden: false,
  })
  expect(wikiEdge.size).toBeCloseTo(0.672)
  expect(evidenceEdge).toMatchObject({
    color: '#e3e5e7',
    hidden: false,
  })
  expect(evidenceEdge.size).toBeCloseTo(0.576)
})

it('uses a quiet background and the focused node color for local exploration', () => {
  expect(graphInteractionPalette(false)).toEqual({
    dimNodeColor: '#d8dadd',
    dimEdgeColor: '#eef0f2',
    focusEdgeStrength: 0.84,
  })
  expect(focusedGraphEdgeColor('#8b5cf6', false)).toBe('#9e76f7')
  expect(focusedGraphEdgeColor('#8b5cf6', true)).toBe('#7e55dd')
})

it('projects global, goal and local graph scopes from existing edges', () => {
  expect(graphScopeNodeIds(graph, {
    mode: 'global', depth: 2, anchorNodeId: null, goalNodeIds: [],
  })).toEqual(new Set(nodes.map((node) => node.node_id)))

  expect(graphScopeNodeIds(graph, {
    mode: 'goal', depth: 1, anchorNodeId: null, goalNodeIds: ['page-neighbor'],
  })).toEqual(new Set(['page-neighbor', 'center']))

  expect(graphScopeNodeIds(graph, {
    mode: 'local', depth: 1, anchorNodeId: 'center', goalNodeIds: [],
  })).toEqual(new Set(['center', 'source-neighbor', 'page-neighbor']))
})

it('finds a deterministic evidence path from the selected node to a goal node', () => {
  expect(shortestGraphPath(graph, 'source-neighbor', ['page-neighbor'])).toEqual({
    nodeIds: ['source-neighbor', 'center', 'page-neighbor'],
    edgeIds: ['edge-source', 'edge-page'],
  })
  expect(shortestGraphPath(graph, 'isolated', ['page-neighbor'])).toBeNull()
})
