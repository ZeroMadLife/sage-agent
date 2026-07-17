import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import {
  createKnowledgeJob,
  fetchKnowledgeGraph,
  fetchKnowledgeGraphNeighborhood,
  fetchKnowledgeJobs,
  ingestKnowledgeSource,
  previewKnowledgeSync,
  rebuildKnowledgeGraph,
  rebuildKnowledgeIndex,
  retryKnowledgeJobItem,
  transitionKnowledgeProposal,
} from '../api/knowledge'
import KnowledgeView from './KnowledgeView.vue'

vi.mock('../components/knowledge', () => ({
  KnowledgeGraphCanvas: {
    props: ['graph', 'selectedNodeId'],
    emits: ['select'],
    template: '<button class="graph-stub" type="button" @click="$emit(\'select\', \'node-page\')">真实图谱 {{ graph.nodes.length }}</button>',
  },
  KnowledgeInspector: {
    props: ['node', 'goal', 'alignments'],
    emits: ['close', 'select'],
    template: '<aside class="inspector-stub">{{ node?.label || goal?.title }} · 能力 {{ alignments.length }}</aside>',
  },
}))

vi.mock('../api/knowledge', () => ({
  applyPendingKnowledgeMigration: vi.fn(),
  cancelKnowledgeJob: vi.fn(),
  createKnowledgeJob: vi.fn(),
  fetchKnowledgeGraph: vi.fn(),
  fetchKnowledgeGraphCommunities: vi.fn(),
  fetchKnowledgeGraphInsights: vi.fn(),
  fetchKnowledgeGraphNeighborhood: vi.fn(),
  fetchKnowledgeIndex: vi.fn(),
  fetchKnowledgeJobs: vi.fn(),
  fetchKnowledgeLearningGoal: vi.fn(),
  fetchKnowledgePages: vi.fn(),
  fetchKnowledgeProposals: vi.fn(),
  fetchKnowledgeSummary: vi.fn(),
  fetchPendingKnowledgeMigration: vi.fn(),
  ingestKnowledgeSource: vi.fn(),
  previewKnowledgeSync: vi.fn(),
  proposeKnowledgeRollback: vi.fn(),
  rebuildKnowledgeGraph: vi.fn(),
  rebuildKnowledgeIndex: vi.fn(),
  retryKnowledgeJobItem: vi.fn(),
  transitionKnowledgeProposal: vi.fn(),
  undoKnowledgeAutoApply: vi.fn(),
}))

const snapshot = {
  graph_revision: 'kgraph_1234567890', workspace_id: 'knowledge-local',
  wiki_watermark: 'kwm_123', projector_id: 'sage.local-graph', projector_version: '1.0.0',
  config_hash: 'sha256:config', status: 'ready' as const, node_count: 2, edge_count: 1,
  warning_count: 0, error: null, created_at: '2026-07-16T00:00:00Z',
  completed_at: '2026-07-16T00:00:01Z', stale: false,
}

const pageNode = {
  node_id: 'node-page', kind: 'page' as const, label: 'Agent Harness',
  page_id: 'page-1', page_revision: 'krev-page', source_id: 'source-1',
  source_revision: 'sha256:source', properties: {},
}

const sourceNode = {
  node_id: 'node-source', kind: 'source' as const, label: 'harness.md',
  page_id: null, page_revision: null, source_id: 'source-1',
  source_revision: 'sha256:source', properties: {},
}

const graph = {
  snapshot,
  nodes: [pageNode, sourceNode],
  edges: [{
    edge_id: 'edge-1', source_node_id: 'node-page', target_node_id: 'node-source',
    kind: 'EVIDENCED_BY' as const, directed: true, weight: 1, confidence: 1,
    extractor_id: 'sage.source', extractor_version: '1.0.0', properties: {},
    evidence: [{
      citation_id: 'kcite-1', chunk_id: 'chunk-1', page_id: 'page-1',
      page_revision: 'krev-page', source_id: 'source-1', source_revision: 'sha256:source',
    }],
  }],
  offset: 0, next_offset: null, has_more: false,
}

const analysis = {
  analysis_revision: 'kanalysis-1', workspace_id: 'knowledge-local',
  graph_revision: snapshot.graph_revision, goal_revision: 'kgoal-1',
  algorithm_id: 'networkx.louvain', algorithm_version: '3.5', seed: 42,
  resolution: 1, threshold: 0.0000001, status: 'ready' as const,
  community_count: 1, insight_count: 1, error: null,
  created_at: '2026-07-16T00:00:00Z', completed_at: '2026-07-16T00:00:01Z',
}

const goal = {
  schema_version: 1, goal_id: 'fullstack-ai', title: '成为全栈 AI 应用工程师',
  description: '以可验证项目建立生产能力。', goal_revision: 'kgoal-1',
  git_commit: 'abc123', structured: true,
  capabilities: [{
    capability_id: 'agent-harness', label: 'Agent Harness', description: '可靠运行时',
    keywords: ['harness'], weight: 1, required: true,
  }],
}

const summary = {
  status: 'ready' as const, workspace_name: 'Sage-knowledge', source_count: 1,
  wiki_page_count: 1, pending_proposal_count: 1, last_synced_at: '2026-07-16T00:00:00Z',
  source_roots: [{ root_id: 'sage-learning', kind: 'obsidian' as const, label: 'Sage Learning' }],
}

const page = {
  page_id: 'page-1', path: 'wiki/sources/harness.md', title: 'Agent Harness',
  current_revision: 'krev-page', updated_at: '2026-07-16T00:00:00Z',
  revisions: [{
    revision_id: 'krev-page', sequence: 1, content_hash: 'sha256:page',
    source_revision: 'sha256:source', proposal_id: 'kprop-1', change_kind: 'ingest' as const,
    git_commit: 'abc123', created_at: '2026-07-16T00:00:00Z',
  }],
}

const proposal = {
  proposal_id: 'kprop-1', source_root_id: 'sage-learning', source_kind: 'obsidian',
  source_relative_path: 'harness.md', source_revision: 'sha256:source', raw_path: 'raw/harness.md',
  page_id: 'page-1', target_path: 'wiki/sources/harness.md', title: 'Agent Harness',
  base_page_revision: '', change_kind: 'ingest' as const, status: 'pending' as const,
  projection_status: 'pending' as const, revision: 0, parse_artifact_id: 'part-1', error: null,
  policy_decision: null, diff: '+ 可审核知识', diff_truncated: false,
  created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z',
}

const indexSummary = {
  status: 'ready' as const, backend: 'sqlite-fts5+hashing', embedding_model: 'sage.hashing',
  embedding_revision: '1.0.0', revision_count: 1, indexed_revision_count: 1,
  active_chunk_count: 4, total_chunk_count: 4, error_count: 0,
}

const syncPlan = {
  plan_id: 'ksync-1', workspace_id: 'knowledge-local', source_root_id: 'sage-learning',
  relative_directory: 'notes', pipeline_version: 'markdown-v1', base_watermark: 4,
  target_watermark: 5, manifest_hash: 'sha256:manifest', status: 'planned',
  added_count: 1, modified_count: 1, deleted_count: 0, total_count: 2,
  has_more: false,
  changes: [
    {
      relative_path: 'notes/new.md', change_kind: 'added' as const,
      previous_revision: null, source_revision: 'sha256:new', idempotency_key: 'idem-new',
    },
    {
      relative_path: 'notes/changed.md', change_kind: 'modified' as const,
      previous_revision: 'sha256:old', source_revision: 'sha256:changed',
      idempotency_key: 'idem-changed',
    },
  ],
  created_at: '2026-07-16T00:00:00Z',
}

beforeEach(async () => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 })
  vi.clearAllMocks()
  const api = await import('../api/knowledge')
  vi.mocked(api.fetchKnowledgeSummary).mockResolvedValue(summary)
  vi.mocked(api.fetchKnowledgePages).mockResolvedValue([page])
  vi.mocked(api.fetchKnowledgeProposals).mockResolvedValue([proposal])
  vi.mocked(api.fetchKnowledgeIndex).mockResolvedValue(indexSummary)
  vi.mocked(api.fetchKnowledgeGraph).mockResolvedValue(graph)
  vi.mocked(api.fetchKnowledgeJobs).mockResolvedValue([])
  vi.mocked(api.fetchKnowledgeGraphCommunities).mockResolvedValue({
    analysis,
    communities: [{
      community_id: 'community-1', label: 'Agent Harness', node_count: 1,
      edge_count: 0, cohesion: 0, properties: {},
    }],
    node_metrics: [{
      node_id: 'node-page', community_id: 'community-1', degree: 1,
      weighted_degree: 1, bridge_score: 0,
    }],
  })
  vi.mocked(api.fetchKnowledgeGraphInsights).mockResolvedValue({
    analysis, goal,
    alignments: [{
      capability_id: 'agent-harness', label: 'Agent Harness', coverage: 0.67,
      status: 'learning', matched_keywords: ['harness'], missing_keywords: ['state machine'],
      matched_node_ids: ['node-page'],
    }],
    insights: [{
      insight_id: 'insight-1', kind: 'capability_gap', severity: 'high',
      title: '补齐状态机', description: '当前知识缺少 state machine。', node_id: null,
      community_id: null, capability_id: 'agent-harness', properties: {},
    }],
  })
  vi.mocked(api.fetchKnowledgeLearningGoal).mockResolvedValue(goal)
  vi.mocked(api.fetchKnowledgeGraphNeighborhood).mockResolvedValue({
    snapshot, center: pageNode, nodes: [pageNode, sourceNode], edges: graph.edges,
  })
  vi.mocked(api.fetchPendingKnowledgeMigration).mockResolvedValue({
    plan_id: 'kmig-1', total: 1, auto_apply_count: 0, retire_count: 0,
    review_count: 1, block_count: 0,
    items: [{
      proposal_id: proposal.proposal_id, source_root_id: 'sage-learning',
      source_relative_path: 'harness.md', disposition: 'review',
      reason_codes: ['external_parser_output'], parser_id: 'external.parser',
    }],
  })
  vi.mocked(api.rebuildKnowledgeIndex).mockResolvedValue(indexSummary)
  vi.mocked(api.rebuildKnowledgeGraph).mockResolvedValue(snapshot)
  vi.mocked(api.ingestKnowledgeSource).mockResolvedValue(proposal)
  vi.mocked(api.previewKnowledgeSync).mockResolvedValue(syncPlan)
  vi.mocked(api.transitionKnowledgeProposal).mockResolvedValue({
    ...proposal, status: 'approved', projection_status: 'complete', revision: 1,
  })
})

async function mountKnowledge() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/knowledge', component: KnowledgeView }],
  })
  await router.push('/knowledge')
  const wrapper = mount(KnowledgeView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

it('renders the versioned graph workspace without a duplicate RAG question form', async () => {
  const wrapper = await mountKnowledge()

  expect(wrapper.text()).toContain('Sage-knowledge')
  expect(wrapper.text()).toContain('成为全栈 AI 应用工程师')
  expect(wrapper.text()).toContain('2 个节点 · 1 条证据连接 · 1 个社区')
  expect(wrapper.get('.graph-stub').text()).toContain('真实图谱 2')
  expect(wrapper.find('[aria-label="Knowledge 主画布"]').exists()).toBe(true)
  expect(wrapper.get('.workbench-dock [role="tab"][aria-selected="true"]').text()).toContain('对话')
  expect(wrapper.text()).toContain('尚未选择知识内容')
  expect(wrapper.find('input[aria-label="知识库问题"]').exists()).toBe(false)
  wrapper.unmount()
})

it('loads revision-bound evidence when a graph node is selected', async () => {
  const wrapper = await mountKnowledge()

  await wrapper.get('.graph-stub').trigger('click')
  await flushPromises()

  expect(fetchKnowledgeGraphNeighborhood).toHaveBeenCalledWith('node-page')
  expect(wrapper.get('[aria-label="当前对话上下文"]').text()).toContain('Agent Harness')
  expect(wrapper.get('.knowledge-harness').attributes('data-active-tab')).toBe('chat')
  await wrapper.findAll('.workbench-dock [role="tab"]')[1].trigger('click')
  expect(wrapper.get('.inspector-stub').text()).toContain('Agent Harness')
  wrapper.unmount()
})

it('opens node details after selecting a graph node on mobile', async () => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
  const wrapper = await mountKnowledge()

  await wrapper.get('.graph-stub').trigger('click')
  await flushPromises()

  expect(wrapper.get('.knowledge-harness').attributes('data-mobile-pane')).toBe('details')
  expect(wrapper.get('.knowledge-harness').attributes('data-active-tab')).toBe('details')
  expect(wrapper.get('.inspector-stub').text()).toContain('Agent Harness')
  wrapper.unmount()
})

it('opens a Wiki page in the detail dock without leaving the Wiki view', async () => {
  const wrapper = await mountKnowledge()

  await wrapper.findAll('.stage-tabs button')[1].trigger('click')
  await wrapper.get('.wiki-main').trigger('click')
  await flushPromises()

  expect(wrapper.get('.knowledge-harness').attributes('data-active-tab')).toBe('details')
  expect(wrapper.findAll('.stage-tabs button')[1].attributes('aria-selected')).toBe('true')
  expect(wrapper.get('.inspector-stub').text()).toContain('Agent Harness')
  wrapper.unmount()
})

it('imports a single source from an authorized root', async () => {
  const wrapper = await mountKnowledge()

  await wrapper.get('button.primary-button').trigger('click')
  const input = wrapper.get('input[aria-label="来源相对路径"]')
  await input.setValue('docs/new.md')
  input.element.closest('form')?.dispatchEvent(new Event('submit'))
  await flushPromises()

  expect(ingestKnowledgeSource).toHaveBeenCalledWith('sage-learning', 'docs/new.md')
  expect(wrapper.text()).toContain('可信结果会自动沉淀')
  wrapper.unmount()
})

it('previews a bounded sync plan before creating its durable directory job', async () => {
  const job = {
    job_id: 'kjob-1', workspace_id: 'knowledge-local', source_root_id: 'sage-learning',
    source_kind: 'obsidian', source_label: 'Sage Learning', relative_directory: 'notes',
    pipeline_version: 'markdown-v1', status: 'running' as const, cancel_requested: false,
    total_items: 2, processed_items: 1, succeeded_items: 1, skipped_items: 0,
    failed_items: 0, cancelled_items: 0, latest_sequence: 1,
    created_at: '', started_at: null, completed_at: null, updated_at: '',
    sync_plan_id: 'ksync-1', items: [],
  }
  vi.mocked(createKnowledgeJob).mockResolvedValue(job)
  const wrapper = await mountKnowledge()

  await wrapper.get('button.primary-button').trigger('click')
  const input = wrapper.get('input[aria-label="来源相对目录"]')
  await input.setValue('notes')
  input.element.closest('form')?.dispatchEvent(new Event('submit'))
  await flushPromises()

  expect(previewKnowledgeSync).toHaveBeenCalledWith('sage-learning', 'notes')
  expect(createKnowledgeJob).not.toHaveBeenCalled()
  expect(wrapper.get('.sync-preview').text()).toContain('发现 2 项变更')
  expect(wrapper.get('.sync-preview').text()).toContain('修改')
  await wrapper.get('.sync-preview .primary-button').trigger('click')
  await flushPromises()

  expect(createKnowledgeJob).toHaveBeenCalledWith('sage-learning', 'notes', 'ksync-1')
  expect(wrapper.text()).toContain('Sage Learning / notes')
  expect(wrapper.text()).toContain('解析中 · 1/2')
  wrapper.unmount()
})

it('does not create a job when the selected source directory is current', async () => {
  vi.mocked(previewKnowledgeSync).mockResolvedValueOnce({
    ...syncPlan,
    plan_id: 'ksync-current',
    added_count: 0,
    modified_count: 0,
    total_count: 0,
    changes: [],
  })
  const wrapper = await mountKnowledge()

  await wrapper.get('button.primary-button').trigger('click')
  const input = wrapper.get('input[aria-label="来源相对目录"]')
  await input.setValue('notes')
  input.element.closest('form')?.dispatchEvent(new Event('submit'))
  await flushPromises()

  expect(wrapper.get('.sync-preview').text()).toContain('当前目录已是最新')
  expect(createKnowledgeJob).not.toHaveBeenCalled()
  wrapper.unmount()
})

it('shows dead-letter errors and retries one failed sync item', async () => {
  const failedItem = {
    item_id: 'kitem-1', job_id: 'kjob-failed', relative_path: 'notes/broken.md',
    source_revision: 'sha256:broken', change_kind: 'modified' as const,
    status: 'dead_letter', attempts: 3, max_attempts: 3, proposal_id: null,
    error: 'parser unavailable', next_attempt_at: null, updated_at: '',
  }
  vi.mocked(fetchKnowledgeJobs).mockResolvedValue([{
    job_id: 'kjob-failed', workspace_id: 'knowledge-local',
    source_root_id: 'sage-learning', source_kind: 'obsidian', source_label: 'Sage Learning',
    relative_directory: 'notes', pipeline_version: 'markdown-v1',
    status: 'completed_with_errors', cancel_requested: false, total_items: 1,
    processed_items: 1, succeeded_items: 0, skipped_items: 0, failed_items: 1,
    cancelled_items: 0, latest_sequence: 4, created_at: '', started_at: '',
    completed_at: '', updated_at: '', sync_plan_id: 'ksync-failed', items: [failedItem],
  }])
  vi.mocked(retryKnowledgeJobItem).mockResolvedValue({
    ...failedItem,
    status: 'retry_wait',
    attempts: 0,
    error: null,
  })
  const wrapper = await mountKnowledge()

  await wrapper.findAll('.stage-tabs button')[2].trigger('click')
  expect(wrapper.text()).toContain('parser unavailable')
  const retry = wrapper.get('.failed-items button')
  expect(retry.text()).toContain('重试 修改：notes/broken.md')
  await retry.trigger('click')
  await flushPromises()

  expect(retryKnowledgeJobItem).toHaveBeenCalledWith('kjob-failed', 'kitem-1')
  wrapper.unmount()
})

it('keeps exceptional proposals in the attention view and approves a revision', async () => {
  const wrapper = await mountKnowledge()

  await wrapper.findAll('.stage-tabs button')[3].trigger('click')
  expect(wrapper.text()).toContain('可信本地内容自动沉淀')
  expect(wrapper.text()).toContain('可审核知识')
  await wrapper.get('button.approve').trigger('click')
  await flushPromises()

  expect(transitionKnowledgeProposal).toHaveBeenCalledWith('kprop-1', 'approve', 0)
  wrapper.unmount()
})

it('rebuilds the graph projection and the revision-aware retrieval index', async () => {
  const wrapper = await mountKnowledge()

  const graphButton = wrapper.findAll('button').find((button) => button.text().includes('同步图谱'))
  expect(graphButton).toBeDefined()
  await graphButton!.trigger('click')
  await flushPromises()
  expect(rebuildKnowledgeGraph).toHaveBeenCalledOnce()

  await wrapper.findAll('.stage-tabs button')[2].trigger('click')
  const indexButton = wrapper.findAll('button').find((button) => button.text().includes('重建索引'))
  expect(indexButton).toBeDefined()
  await indexButton!.trigger('click')
  await flushPromises()
  expect(rebuildKnowledgeIndex).toHaveBeenCalledOnce()
  wrapper.unmount()
})

it('shows a truthful empty state when the current graph has no nodes', async () => {
  vi.mocked(fetchKnowledgeGraph).mockResolvedValue({
    ...graph, snapshot: { ...snapshot, node_count: 0, edge_count: 0 }, nodes: [], edges: [],
  })
  const wrapper = await mountKnowledge()

  expect(wrapper.text()).toContain('当前 revision 还没有图谱节点')
  expect(wrapper.find('.graph-stub').exists()).toBe(false)
  wrapper.unmount()
})

it('keeps single-file ingestion available when durable jobs are disabled', async () => {
  vi.mocked(fetchKnowledgeJobs).mockRejectedValue(new Error('knowledge jobs are not configured'))
  const wrapper = await mountKnowledge()

  await wrapper.findAll('.stage-tabs button')[2].trigger('click')
  expect(wrapper.text()).toContain('持久任务尚未启用')
  await wrapper.get('button.primary-button').trigger('click')
  expect(wrapper.get('input[aria-label="来源相对路径"]').attributes('disabled')).toBeUndefined()
  expect(wrapper.get('input[aria-label="来源相对目录"]').element.closest('form')?.querySelector('button')?.disabled).toBe(true)
  wrapper.unmount()
})
