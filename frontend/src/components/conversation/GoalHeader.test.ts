import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import type { CodingThreadGoal } from '../../types/api'
import GoalHeader from './GoalHeader.vue'

function goal(): CodingThreadGoal {
  return {
    goal_id: 'goal-1', revision: 3,
    description: '掌握可恢复的 Agent Harness',
    completion_criteria: ['能解释恢复边界', '能完成一次真实演练'],
    status: 'active',
    evaluation: {
      status: 'continue', blocker: 'missing_evidence', evidence_refs: ['event-1'],
      next_action: '完成一次恢复演练', source_run_id: 'run-1', evaluated_at: '2026-07-22T00:00:00Z',
      criteria: [
        { index: 0, status: 'met', evidence_refs: ['event-1'] },
        { index: 1, status: 'unmet', evidence_refs: [] },
      ],
    },
    continuation: {
      mode: 'manual', max_auto_followups: 0, auto_followups_started: 0,
      no_progress_streak: 0, last_progress_fingerprint: '', stop_reason: null,
    },
    created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z',
  }
}

it('shows the current goal and its factual next action', () => {
  const wrapper = mount(GoalHeader, {
    props: { sessionTitle: 'Harness 学习', threadGoal: goal(), runStatus: 'completed' },
    slots: { actions: '<button type="button">历史</button>' },
  })

  expect(wrapper.get('[aria-label="当前目标"]').text()).toContain('掌握可恢复的 Agent Harness')
  expect(wrapper.text()).toContain('完成一次恢复演练')
  expect(wrapper.text()).toContain('1 / 2 条标准已有证据')
  expect(wrapper.text()).toContain('历史')
})

it('uses the real session title when no Thread Goal exists', () => {
  const wrapper = mount(GoalHeader, {
    props: { sessionTitle: '新的研究会话', threadGoal: null, runStatus: 'idle' },
  })

  expect(wrapper.text()).toContain('新的研究会话')
  expect(wrapper.text()).toContain('尚未绑定长期目标')
  expect(wrapper.text()).not.toContain('100%')
})
