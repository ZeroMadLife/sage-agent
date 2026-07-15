import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import {
  applyPendingKnowledgeMigration,
  createKnowledgeJob,
  fetchKnowledgeJobs,
  fetchPendingKnowledgeMigration,
  fetchKnowledgePages,
  fetchKnowledgeProposals,
  fetchKnowledgeSummary,
  ingestKnowledgeSource,
  transitionKnowledgeProposal,
  undoKnowledgeAutoApply,
} from '../api/knowledge'
import KnowledgeView from './KnowledgeView.vue'

vi.mock('../api/knowledge', () => ({
  applyPendingKnowledgeMigration: vi.fn(),
  buildKnowledgeJobStreamUrl: vi.fn(),
  cancelKnowledgeJob: vi.fn(),
  createKnowledgeJob: vi.fn(),
  fetchKnowledgeJob: vi.fn(),
  fetchKnowledgeJobs: vi.fn(),
  fetchPendingKnowledgeMigration: vi.fn(),
  fetchKnowledgePages: vi.fn(),
  fetchKnowledgeProposals: vi.fn(),
  fetchKnowledgeSummary: vi.fn(),
  ingestKnowledgeSource: vi.fn(),
  parseKnowledgeJobEvent: vi.fn(),
  proposeKnowledgeRollback: vi.fn(),
  retryKnowledgeJobItem: vi.fn(),
  transitionKnowledgeProposal: vi.fn(),
  undoKnowledgeAutoApply: vi.fn(),
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
  policy_decision: null,
  diff: '+可审核知识', diff_truncated: false, created_at: '', updated_at: '',
}

beforeEach(() => {
  vi.mocked(fetchKnowledgeSummary).mockReset().mockResolvedValue(summary)
  vi.mocked(fetchKnowledgeProposals).mockReset().mockResolvedValue([proposal])
  vi.mocked(fetchKnowledgePages).mockReset().mockResolvedValue([])
  vi.mocked(fetchKnowledgeJobs).mockReset().mockResolvedValue([])
  vi.mocked(fetchPendingKnowledgeMigration).mockReset().mockResolvedValue({
    plan_id: 'kmig-review',
    total: 1,
    auto_apply_count: 0,
    retire_count: 0,
    review_count: 1,
    block_count: 0,
    items: [{
      proposal_id: 'kprop-1', source_root_id: 'sage-learning',
      source_relative_path: 'harness.md', disposition: 'review',
      reason_codes: ['external_parser_output'], parser_id: 'external.parser',
    }],
  })
  vi.mocked(applyPendingKnowledgeMigration).mockReset()
  vi.mocked(createKnowledgeJob).mockReset()
  vi.mocked(ingestKnowledgeSource).mockReset().mockResolvedValue(proposal)
  vi.mocked(transitionKnowledgeProposal).mockReset().mockResolvedValue({
    ...proposal, status: 'approved', projection_status: 'complete', revision: 1,
  })
  vi.mocked(undoKnowledgeAutoApply).mockReset()
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

it('shows and undoes a current auto-applied knowledge revision', async () => {
  const autoApplied = {
    ...proposal,
    status: 'approved' as const,
    projection_status: 'complete' as const,
    revision: 1,
    policy_decision: {
      decision_id: 'kpol-1', policy_id: 'sage.knowledge-autonomy', policy_version: '1.0.0',
      risk_level: 'low' as const, action: 'auto_apply' as const,
      reason_codes: ['deterministic_local_parser'], applied_page_revision: 'krev-1',
      undo_available: true, undo_proposal_id: null, undo_page_revision: null, undone_at: null,
    },
  }
  vi.mocked(fetchKnowledgeProposals).mockResolvedValue([autoApplied])
  vi.mocked(undoKnowledgeAutoApply).mockResolvedValue({
    ...autoApplied,
    change_kind: 'retraction',
    policy_decision: {
      ...autoApplied.policy_decision,
      risk_level: 'high',
      action: 'require_review',
      applied_page_revision: null,
      undo_available: false,
    },
  })
  const wrapper = await mountKnowledge()

  const undo = wrapper.findAll('button').find((button) => button.text().includes('撤销自动沉淀'))
  expect(undo).toBeDefined()
  await undo!.trigger('click')
  await flushPromises()

  expect(undoKnowledgeAutoApply).toHaveBeenCalledWith('kprop-1', 'krev-1')
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

it('organizes safe historical proposals in one action and keeps only exceptions visible', async () => {
  const actionable = {
    plan_id: 'kmig-actionable', total: 3, auto_apply_count: 2, retire_count: 1,
    review_count: 0, block_count: 0,
    items: [
      { proposal_id: 'legacy-1', source_root_id: 'sage-learning', source_relative_path: 'a.md', disposition: 'auto_apply' as const, reason_codes: ['trusted_local_reparse'], parser_id: 'sage.markdown' },
      { proposal_id: 'legacy-2', source_root_id: 'sage-learning', source_relative_path: 'b.md', disposition: 'auto_apply' as const, reason_codes: ['trusted_local_reparse'], parser_id: 'sage.markdown' },
      { proposal_id: 'legacy-3', source_root_id: 'sage-learning', source_relative_path: 'gone.md', disposition: 'retire' as const, reason_codes: ['source_missing'], parser_id: null },
    ],
  }
  vi.mocked(fetchPendingKnowledgeMigration)
    .mockResolvedValueOnce(actionable)
    .mockResolvedValue({ ...actionable, plan_id: 'kmig-empty', total: 0, auto_apply_count: 0, retire_count: 0, items: [] })
  vi.mocked(applyPendingKnowledgeMigration).mockResolvedValue({
    plan_id: actionable.plan_id, status: 'completed', total: 3,
    auto_applied_count: 2, retired_count: 1, review_count: 0,
    blocked_count: 0, error_count: 0, items: [],
  })

  const wrapper = await mountKnowledge()

  expect(wrapper.text()).toContain('无需逐条审核')
  const apply = wrapper.findAll('button').find((button) => button.text().includes('一键整理 3 条'))
  expect(apply).toBeDefined()
  await apply!.trigger('click')
  await flushPromises()

  expect(applyPendingKnowledgeMigration).toHaveBeenCalledWith('kmig-actionable')
  expect(wrapper.text()).toContain('本次已自动沉淀 2 条')
  expect(wrapper.text()).toContain('当前没有异常')
  wrapper.unmount()
})
