import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import {
  createKnowledgeJob,
  fetchKnowledgeJobs,
  fetchKnowledgePages,
  fetchKnowledgeProposals,
  fetchKnowledgeSummary,
  ingestKnowledgeSource,
  transitionKnowledgeProposal,
} from '../api/knowledge'
import KnowledgeView from './KnowledgeView.vue'

vi.mock('../api/knowledge', () => ({
  buildKnowledgeJobStreamUrl: vi.fn(),
  cancelKnowledgeJob: vi.fn(),
  createKnowledgeJob: vi.fn(),
  fetchKnowledgeJob: vi.fn(),
  fetchKnowledgeJobs: vi.fn(),
  fetchKnowledgePages: vi.fn(),
  fetchKnowledgeProposals: vi.fn(),
  fetchKnowledgeSummary: vi.fn(),
  ingestKnowledgeSource: vi.fn(),
  parseKnowledgeJobEvent: vi.fn(),
  proposeKnowledgeRollback: vi.fn(),
  retryKnowledgeJobItem: vi.fn(),
  transitionKnowledgeProposal: vi.fn(),
}))

const summary = {
  status: 'ready' as const,
  workspace_name: 'Sage-knowledge',
  source_count: 1,
  wiki_page_count: 0,
  pending_proposal_count: 1,
  last_synced_at: null,
  source_roots: [{ root_id: 'sage-learning', kind: 'obsidian' as const, label: 'Sage Learning' }],
}

const proposal = {
  proposal_id: 'kprop-1', source_root_id: 'sage-learning', source_kind: 'obsidian',
  source_relative_path: 'harness.md', source_revision: 'sha256:abc', raw_path: 'raw/source.md',
  page_id: 'page-1', target_path: 'wiki/sources/harness.md', title: 'Agent Harness',
  base_page_revision: '', change_kind: 'ingest' as const, status: 'pending' as const,
  projection_status: 'pending' as const, revision: 0, parse_artifact_id: 'part-1', error: null,
  diff: '+可审核知识', diff_truncated: false, created_at: '', updated_at: '',
}

beforeEach(() => {
  vi.mocked(fetchKnowledgeSummary).mockReset().mockResolvedValue(summary)
  vi.mocked(fetchKnowledgeProposals).mockReset().mockResolvedValue([proposal])
  vi.mocked(fetchKnowledgePages).mockReset().mockResolvedValue([])
  vi.mocked(fetchKnowledgeJobs).mockReset().mockResolvedValue([])
  vi.mocked(createKnowledgeJob).mockReset()
  vi.mocked(ingestKnowledgeSource).mockReset().mockResolvedValue(proposal)
  vi.mocked(transitionKnowledgeProposal).mockReset().mockResolvedValue({
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

it('renders real knowledge status and review controls', async () => {
  const wrapper = await mountKnowledge()

  expect(wrapper.text()).toContain('Sage-knowledge')
  expect(wrapper.text()).toContain('Agent Harness')
  expect(wrapper.text()).toContain('可审核知识')
  expect(wrapper.find('input[aria-label="来源相对路径"]').exists()).toBe(true)
  expect(wrapper.get('button.approve').text()).toContain('批准并提交')
  wrapper.unmount()
})

it('creates an ingest proposal and approves a pending proposal', async () => {
  const wrapper = await mountKnowledge()
  await wrapper.get('input[aria-label="来源相对路径"]').setValue('new.md')
  await wrapper.get('input[aria-label="来源相对路径"]').element.closest('form')?.dispatchEvent(new Event('submit'))
  await flushPromises()
  expect(ingestKnowledgeSource).toHaveBeenCalledWith('sage-learning', 'new.md')

  await wrapper.get('button.approve').trigger('click')
  await flushPromises()
  expect(transitionKnowledgeProposal).toHaveBeenCalledWith('kprop-1', 'approve', 0)
  wrapper.unmount()
})

it('creates a durable batch job from a relative directory', async () => {
  const job = {
    job_id: 'kjob-1', workspace_id: 'knowledge-local', source_root_id: 'sage-learning',
    source_kind: 'obsidian', source_label: 'Sage Learning', relative_directory: 'notes',
    pipeline_version: 'p2.2-a-markdown-v1', status: 'completed' as const,
    cancel_requested: false, total_items: 2, processed_items: 2, succeeded_items: 2,
    skipped_items: 0, failed_items: 0, cancelled_items: 0, latest_sequence: 1,
    created_at: '', started_at: null, completed_at: null, updated_at: '', items: [],
  }
  vi.mocked(createKnowledgeJob).mockResolvedValue(job)
  const wrapper = await mountKnowledge()

  const directory = wrapper.get('input[aria-label="来源相对目录"]')
  await directory.setValue('notes')
  directory.element.closest('form')?.dispatchEvent(new Event('submit'))
  await flushPromises()

  expect(createKnowledgeJob).toHaveBeenCalledWith('sage-learning', 'notes')
  expect(wrapper.text()).toContain('已完成')
  wrapper.unmount()
})

it('keeps the P2.1 review workspace usable when durable jobs are disabled', async () => {
  vi.mocked(fetchKnowledgeJobs).mockRejectedValue(new Error('knowledge jobs are not configured'))

  const wrapper = await mountKnowledge()

  expect(wrapper.text()).toContain('Sage-knowledge')
  expect(wrapper.text()).toContain('持久任务未启用')
  expect(wrapper.get('button.approve').attributes('disabled')).toBeUndefined()
  expect(wrapper.get('input[aria-label="来源相对目录"]').attributes('disabled')).toBeDefined()
  wrapper.unmount()
})
