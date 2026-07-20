import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type {
  CodingKnowledgeSourceProposal,
  CodingKnowledgeSourceProposalDetail,
} from '../../types/api'
import KnowledgeSourceProposalReviewList from './KnowledgeSourceProposalReviewList.vue'

function proposal(): CodingKnowledgeSourceProposal {
  return {
    proposal_id: 'ksprop_1', thread_id: 'session-1', run_id: 'run-web',
    artifact_ref: 'sage://coding/session-1/run-web/artifacts/fetch-1', source_kind: 'web',
    canonical_url: 'https://docs.example.com/guide', title: '官方学习指南',
    media_type: 'text/html', retrieved_at: '2026-07-18T09:30:00Z',
    content_hash: 'a'.repeat(64), reason: '补充当前目标缺失的官方证据',
    evidence_refs: ['wcite_official'], status: 'pending', revision: 1,
    target_root_id: 'sage-learning', target_relative_path: '', job_id: null,
    last_error: null, decided_by: null, decided_at: null,
    created_at: '2026-07-18T09:31:00Z', updated_at: '2026-07-18T09:31:00Z',
  }
}

function detail(): CodingKnowledgeSourceProposalDetail {
  const current = proposal()
  return {
    proposal: current,
    events: [{
      event_id: 'event-1', proposal_id: current.proposal_id, sequence: 1,
      event_type: 'proposal_created', revision: 1,
      detail: { source_kind: 'web' }, created_at: current.created_at,
    }],
  }
}

describe('KnowledgeSourceProposalReviewList', () => {
  it('renders a browser-safe source receipt and revision-bound actions', async () => {
    const current = proposal()
    const wrapper = mount(KnowledgeSourceProposalReviewList, {
      props: { proposals: [current], compact: true },
    })

    expect(wrapper.classes()).toContain('compact')
    expect(wrapper.text()).toContain('官方学习指南')
    expect(wrapper.text()).toContain('docs.example.com')
    expect(wrapper.text()).toContain('补充当前目标缺失的官方证据')
    expect(wrapper.text()).toContain('wcite_official')
    expect(wrapper.text()).not.toContain(current.artifact_ref)

    await wrapper.get('[data-action="approve-source"]').trigger('click')
    await wrapper.get('[data-action="reject-source"]').trigger('click')
    expect(wrapper.emitted('approve')).toEqual([[current.proposal_id, current.revision]])
    expect(wrapper.emitted('reject')).toEqual([[current.proposal_id, current.revision]])
  })

  it('loads and renders the append-only review trail on demand', async () => {
    const current = proposal()
    const wrapper = mount(KnowledgeSourceProposalReviewList, {
      props: { proposals: [current] },
    })
    const audit = wrapper.get('details')
    ;(audit.element as HTMLDetailsElement).open = true
    await audit.trigger('toggle')

    expect(wrapper.emitted('loadDetail')).toEqual([[current.proposal_id]])
    await wrapper.setProps({ details: { [current.proposal_id]: detail() } })
    expect(wrapper.text()).toContain('提案已创建')
    expect(wrapper.text()).toContain('revision 1')
  })

  it('keeps the proposal visible while reporting a CAS conflict', () => {
    const wrapper = mount(KnowledgeSourceProposalReviewList, {
      props: {
        proposals: [proposal()],
        error: '知识来源提案已发生变化，请刷新后重试',
      },
    })

    expect(wrapper.get('[role="alert"]').text()).toContain('请刷新后重试')
    expect(wrapper.findAll('.source-proposal-row')).toHaveLength(1)
  })

  it('renders an applying proposal as a non-repeatable recovery state', () => {
    const current = { ...proposal(), status: 'applying' as const, revision: 2 }
    const wrapper = mount(KnowledgeSourceProposalReviewList, {
      props: { proposals: [current] },
    })

    expect(wrapper.text()).toContain('正在入库')
    expect(wrapper.get('[data-action="approve-source"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-action="reject-source"]').attributes('disabled')).toBeDefined()
  })
})
