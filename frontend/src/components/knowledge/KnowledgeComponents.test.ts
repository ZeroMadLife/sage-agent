import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { fetchKnowledgeCitation, fetchKnowledgePage } from '../../api/knowledge'
import type {
  KnowledgeGraph,
  KnowledgeGraphCommunities,
  KnowledgeGraphNeighborhood,
} from '../../types/api'
import KnowledgeGraphCanvas from './KnowledgeGraphCanvas.vue'
import KnowledgeGraphToolbar from './KnowledgeGraphToolbar.vue'
import KnowledgeInspector from './KnowledgeInspector.vue'
import KnowledgeSourceRail from './KnowledgeSourceRail.vue'
import KnowledgeWorkspaceHeader from './KnowledgeWorkspaceHeader.vue'
import { selectedNodePresentation } from './knowledgeGraphPresentation'

vi.mock('../../api/knowledge', () => ({
  fetchKnowledgeCitation: vi.fn(),
  fetchKnowledgePage: vi.fn(),
}))

vi.hoisted(() => {
  Object.defineProperty(globalThis, 'WebGLRenderingContext', {
    configurable: true,
    value: class WebGLRenderingContext {},
  })
  Object.defineProperty(globalThis, 'WebGL2RenderingContext', {
    configurable: true,
    value: class WebGL2RenderingContext {},
  })
})

const snapshot = {
  graph_revision: 'kgraph-test', workspace_id: 'knowledge-local', wiki_watermark: 'kwm-test',
  projector_id: 'sage.local-graph', projector_version: '1.0.0', config_hash: 'sha256:test',
  status: 'ready' as const, node_count: 2, edge_count: 1, warning_count: 0, error: null,
  created_at: '', completed_at: '', stale: false,
}

const pageNode = {
  node_id: 'page-1', kind: 'page' as const, label: 'Agent Harness', page_id: 'page-1',
  page_revision: 'krev-page', source_id: 'source-1', source_revision: 'sha256:source',
  properties: {},
}

const sourceNode = {
  node_id: 'source-1', kind: 'source' as const, label: 'harness.md', page_id: null,
  page_revision: null, source_id: 'source-1', source_revision: 'sha256:source',
  properties: { relative_path: 'notes/harness.md', source_kind: 'obsidian' },
}

const edge = {
  edge_id: 'edge-1', source_node_id: 'page-1', target_node_id: 'source-1',
  kind: 'EVIDENCED_BY' as const, directed: true, weight: 1, confidence: 1,
  extractor_id: 'sage.source', extractor_version: '1.0.0', properties: {},
  evidence: [{
    citation_id: 'kcite-1', chunk_id: 'chunk-1', page_id: 'page-1',
    page_revision: 'krev-page', source_id: 'source-1', source_revision: 'sha256:source',
  }],
}

const graph: KnowledgeGraph = {
  snapshot, nodes: [pageNode, sourceNode], edges: [edge],
  offset: 0, next_offset: null, has_more: false,
}

const communities: KnowledgeGraphCommunities = {
  analysis: {
    analysis_revision: 'analysis-1', workspace_id: 'knowledge-local',
    graph_revision: 'kgraph-test', goal_revision: 'kgoal-test',
    algorithm_id: 'networkx.louvain', algorithm_version: '3.5', seed: 42,
    resolution: 1, threshold: 0.0000001, status: 'ready', community_count: 1,
    insight_count: 0, error: null, created_at: '', completed_at: '',
  },
  communities: [{
    community_id: 'community-1', label: 'Harness', node_count: 2,
    edge_count: 1, cohesion: 1, properties: {},
  }],
  node_metrics: [
    { node_id: 'page-1', community_id: 'community-1', degree: 1, weighted_degree: 1, bridge_score: 0 },
    { node_id: 'source-1', community_id: 'community-1', degree: 1, weighted_degree: 1, bridge_score: 0 },
  ],
}

beforeEach(() => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
  localStorage.clear()
  vi.mocked(fetchKnowledgeCitation).mockResolvedValue({
    citation_id: 'kcite-1', chunk_id: 'chunk-1', page_id: 'page-1',
    page_revision: 'krev-page', page_path: 'wiki/sources/harness.md',
    source_id: 'source-1', source_revision: 'sha256:source', source_kind: 'obsidian',
    source_relative_path: 'notes/harness.md', block_id: 'block-1', ordinal: 0,
    title: 'Agent Harness', heading_path: ['Harness', 'Recovery'], page_number: null,
    excerpt: 'Harness 使用可恢复的状态机保存执行证据。', token_count: 12, truncated: false,
  })
  vi.mocked(fetchKnowledgePage).mockResolvedValue({
    page_id: 'page-1', path: 'wiki/sources/harness.md', title: 'Agent Harness',
    updated_at: '', revision: {
      revision_id: 'krev-page', sequence: 1, content_hash: 'sha256:page',
      source_revision: 'sha256:source', proposal_id: 'kprop-1', change_kind: 'ingest',
      git_commit: 'abc123', created_at: '',
    },
    content: '# Agent Harness\n\n可恢复执行。\n\n<script>alert(1)</script>', truncated: false,
  })
})

it('keeps the selected node color and disables Sigma white highlight overlays', () => {
  const presentation = selectedNodePresentation('#16a085', 5)
  expect(presentation.color).toBe('#16a085')
  expect(presentation.size).toBeCloseTo(6.9)
  expect(presentation.highlighted).toBe(false)
  expect(presentation.zIndex).toBe(6)
})

it('emits the compact graph navigation controls without owning graph state', async () => {
  const toolbar = mount(KnowledgeGraphToolbar, {
    props: {
      query: '', scope: 'global', depth: 1, colorMode: 'community',
      visibleKinds: ['page', 'source'], canUseGoal: true, canUseLocal: true,
      canShowGoalPath: true, showGoalPath: false,
    },
  })

  await toolbar.get('input[type="search"]').setValue('Harness')
  await toolbar.get('.scope-control button:last-child').trigger('click')
  await toolbar.get('.path-action').trigger('click')
  await toolbar.get('.kind-filter input').setValue(false)
  expect(toolbar.emitted('update:query')).toEqual([['Harness']])
  expect(toolbar.emitted('update:scope')).toEqual([['local']])
  expect(toolbar.emitted('toggleGoalPath')).toHaveLength(1)
  expect(toolbar.emitted('toggleKind')).toEqual([['page']])

  const rail = mount(KnowledgeSourceRail, { props: { activeMode: 'graph', attentionCount: 12 } })
  await rail.get('button[data-mode="wiki"]').trigger('click')
  expect(rail.emitted('select')).toEqual([['wiki']])
  expect(rail.find('.rail-import').exists()).toBe(false)
})

it('keeps workspace status and source actions in one restrained header', async () => {
  const wrapper = mount(KnowledgeWorkspaceHeader, {
    props: {
      workspaceName: 'Sage-knowledge', connected: true, stale: false,
      syncing: false,
    },
  })

  expect(wrapper.text()).toContain('来源与索引已连接')
  expect(wrapper.text()).not.toContain('kgraph_')
  expect(wrapper.text()).toContain('导入知识库')
  await wrapper.get('button[aria-label="同步图谱"]').trigger('click')
  await wrapper.get('.import-action').trigger('click')
  expect(wrapper.emitted('sync')).toHaveLength(1)
  expect(wrapper.emitted('import')).toHaveLength(1)
})

it('keeps the interactive graph on mobile within the compact performance budget', async () => {
  const wrapper = mount(KnowledgeGraphCanvas, {
    props: {
      graph, communities, selectedNodeId: null, colorMode: 'community',
      visibleKinds: ['page', 'source'], query: '',
    },
  })

  expect(wrapper.find('.mobile-node-list').exists()).toBe(false)
  expect(wrapper.get('.sigma-container').attributes('aria-label')).toBe('本地知识图谱')
  wrapper.unmount()
})

it('degrades an oversized compact graph to a bounded node list', async () => {
  const oversizedGraph: KnowledgeGraph = {
    snapshot: { ...snapshot, node_count: 1201, edge_count: 0 },
    nodes: Array.from({ length: 1201 }, (_, index) => ({
      ...pageNode,
      node_id: `page-${index + 1}`,
      page_id: `page-${index + 1}`,
      label: index === 0 ? 'Agent Harness' : `Page ${index + 1}`,
    })),
    edges: [],
    offset: 0,
    next_offset: null,
    has_more: false,
  }
  const wrapper = mount(KnowledgeGraphCanvas, {
    props: {
      graph: oversizedGraph, communities: null, selectedNodeId: null, colorMode: 'community',
      visibleKinds: ['page'], query: '',
    },
  })

  expect(wrapper.find('.sigma-container').exists()).toBe(false)
  expect(wrapper.findAll('.mobile-node-list button')).toHaveLength(80)
  await wrapper.setProps({ query: 'agent' })
  await flushPromises()
  expect(wrapper.findAll('.mobile-node-list button')).toHaveLength(1)
  await wrapper.get('.mobile-node-list button').trigger('click')
  expect(wrapper.emitted('select')).toEqual([['page-1']])
  wrapper.unmount()
})

it('waits for a measurable desktop container before mounting Sigma', async () => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 })
  const wrapper = mount(KnowledgeGraphCanvas, {
    props: {
      graph, communities, selectedNodeId: null, colorMode: 'community',
      visibleKinds: ['page', 'source'], query: '',
    },
  })

  await flushPromises()
  expect(wrapper.find('.graph-error').exists()).toBe(false)
  expect(wrapper.find('.mobile-node-list').exists()).toBe(false)
  expect(wrapper.get('.sigma-container').attributes('aria-label')).toBe('本地知识图谱')
  wrapper.unmount()
})

it('keeps selection detail out of the canvas overlay and limits the legend', () => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 })
  const wrapper = mount(KnowledgeGraphCanvas, {
    props: {
      graph, communities, selectedNodeId: 'page-1', colorMode: 'community',
      visibleKinds: ['page', 'source'], query: '',
    },
  })

  expect(wrapper.find('.graph-focus-summary').exists()).toBe(false)
  expect(wrapper.find('.graph-global-summary').exists()).toBe(false)
  expect(wrapper.get('.graph-legend-panel').attributes('data-mode')).toBe('community')
  expect(wrapper.get('.graph-legend-panel').text()).toBe('Harness')
  wrapper.unmount()
})

it('shows revision evidence and one-hop relations in the inspector', async () => {
  const neighborhood: KnowledgeGraphNeighborhood = {
    snapshot, center: pageNode, nodes: [pageNode, sourceNode], edges: [edge],
  }
  const wrapper = mount(KnowledgeInspector, {
    attachTo: document.body,
    props: {
      node: pageNode,
      page: {
        page_id: 'page-1', path: 'wiki/sources/harness.md', title: 'Agent Harness',
        current_revision: 'krev-page', updated_at: '', revisions: [],
      },
      neighborhood,
      insights: [],
      goal: null,
      alignments: [],
      communities: communities.communities,
      communityId: 'community-1',
      loading: false,
      compact: true,
    },
  })

  expect(wrapper.text()).toContain('Agent Harness')
  expect(wrapper.text()).not.toContain('wiki/sources/harness.md')
  await flushPromises()
  expect(document.activeElement).toBe(wrapper.get('button[aria-label="关闭知识详情"]').element)
  await wrapper.findAll('.inspector-tabs button')[1].trigger('click')
  await flushPromises()
  expect(fetchKnowledgePage).toHaveBeenCalledWith('page-1')
  expect(wrapper.get('.wiki-content').text()).toContain('可恢复执行')
  expect(wrapper.find('.wiki-content script').exists()).toBe(false)
  await wrapper.findAll('.inspector-tabs button')[2].trigger('click')
  expect(wrapper.text()).toContain('notes/harness.md')
  expect(wrapper.text()).toContain('来源证据')
  expect(wrapper.text()).toContain('sha256:source')
  await wrapper.get('button[aria-label="查看 notes/harness.md 的证据片段"]').trigger('click')
  await flushPromises()
  expect(fetchKnowledgeCitation).toHaveBeenCalledWith('kcite-1')
  expect(wrapper.text()).toContain('Harness 使用可恢复的状态机保存执行证据。')
  expect(wrapper.text()).toContain('Harness / Recovery')
  await wrapper.findAll('.inspector-tabs button')[3].trigger('click')
  await wrapper.get('.relation-list button').trigger('click')
  expect(wrapper.emitted('select')).toEqual([['source-1']])
  expect(wrapper.find('button[aria-label="在对话中研究此节点"]').exists()).toBe(false)
  await wrapper.get('.knowledge-inspector').trigger('keydown', { key: 'Escape' })
  expect(wrapper.emitted('close')).toHaveLength(1)
  wrapper.unmount()
})

it('reads a canonical Wiki page that is not yet projected into the graph', async () => {
  const wrapper = mount(KnowledgeInspector, {
    props: {
      node: null,
      page: {
        page_id: 'page-1', path: 'wiki/sources/harness.md', title: 'Agent Harness',
        current_revision: 'krev-page', updated_at: '', revisions: [],
      },
      neighborhood: null,
      insights: [],
      goal: null,
      alignments: [],
      communities: [],
      communityId: null,
      loading: false,
      compact: false,
    },
  })

  expect(wrapper.findAll('.inspector-tabs button')).toHaveLength(2)
  await wrapper.findAll('.inspector-tabs button')[1].trigger('click')
  await flushPromises()

  expect(fetchKnowledgePage).toHaveBeenCalledWith('page-1')
  expect(wrapper.get('.wiki-content').text()).toContain('可恢复执行')
  wrapper.unmount()
})
