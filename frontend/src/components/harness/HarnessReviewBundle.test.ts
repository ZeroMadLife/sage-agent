import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { HarnessReviewBundle } from '../../harness/reviewBundle'
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
})
