import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { HarnessReviewBundle } from '../../harness/reviewBundle'
import type { CodingKnowledgeSourceProposal } from '../../types/api'
import HarnessReviewBundleView from './HarnessReviewBundle.vue'

const bundle: HarnessReviewBundle = {
  runId: 'run-1',
  evidence: {
    status: 'ready', query: 'Harness 恢复', omittedCount: 0,
    items: [{
      id: 'cite-1', title: 'Harness 恢复机制', source: 'docs/harness.md',
      pageRevision: 'page-rev-1', sourceRevision: 'source-rev-1', excerpt: '恢复证据。',
    }],
  },
  practice: {
    status: 'complete', headline: '验证完成', toolCount: 2, completedToolCount: 2,
    failedToolCount: 0, approvalCount: 1, durationMs: 2_500,
    changedFiles: ['frontend/src/App.vue'],
  },
  deposit: {
    status: 'review', proposalId: 'proposal-1', revision: 2,
    items: ['恢复必须由 timeline 驱动。'], source: 'reflection',
  },
}

const sourceProposal: CodingKnowledgeSourceProposal = {
  proposal_id: 'ksprop_1', thread_id: 'session-1', run_id: 'run-1',
  artifact_ref: 'sage://coding/session-1/run-1/artifacts/fetch', source_kind: 'web',
  canonical_url: 'https://example.com/official', title: '官方证据', media_type: 'text/html',
  retrieved_at: '', content_hash: 'a'.repeat(64), reason: '补齐缺少的来源',
  evidence_refs: ['wcite_1'], status: 'pending', revision: 1,
  target_root_id: 'sage-learning', target_relative_path: '', job_id: null,
  last_error: null, decided_by: null, decided_at: null, created_at: '', updated_at: '',
}

describe('HarnessReviewBundle', () => {
  it('renders traceable outcomes without hiding their empty-state boundary', () => {
    const wrapper = mount(HarnessReviewBundleView, { props: { bundle } })

    expect(wrapper.get('.review-evidence').text()).toContain('Harness 恢复机制')
    expect(wrapper.get('.review-evidence').text()).toContain('docs/harness.md')
    expect(wrapper.get('.review-practice').text()).toContain('验证完成')
    expect(wrapper.get('.review-practice').text()).toContain('2 项工具')
    expect(wrapper.get('.review-deposit').text()).toContain('恢复必须由 timeline 驱动。')
  })

  it('emits revision-bound proposal decisions', async () => {
    const wrapper = mount(HarnessReviewBundleView, { props: { bundle } })

    await wrapper.get('button[data-action="approve"]').trigger('click')
    await wrapper.get('button[data-action="reject"]').trigger('click')

    expect(wrapper.emitted('approveDeposit')).toEqual([['proposal-1', 2]])
    expect(wrapper.emitted('rejectDeposit')).toEqual([['proposal-1', 2]])
  })

  it('embeds source review without treating it as committed knowledge', async () => {
    const wrapper = mount(HarnessReviewBundleView, {
      props: { bundle: { ...bundle, deposit: { ...bundle.deposit, status: 'empty' } }, sourceProposals: [sourceProposal] },
    })

    expect(wrapper.get('.review-deposit').text()).toContain('官方证据')
    expect(wrapper.get('.review-deposit').text()).toContain('确认后才进入 Knowledge 或 Memory')
    await wrapper.get('[data-action="reject-source"]').trigger('click')
    expect(wrapper.emitted('rejectSource')).toEqual([[sourceProposal.proposal_id, 1]])
  })
})
