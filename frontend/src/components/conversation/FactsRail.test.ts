import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import type { HarnessProjection, HarnessSurfaceContext } from '../../harness/types'
import { emptyHarnessReviewBundle } from '../../harness/reviewBundle'
import type { CodingThreadGoal } from '../../types/api'
import FactsRail from './FactsRail.vue'

function projection(status: HarnessProjection['status'] = 'failed'): HarnessProjection {
  const stageStatus = status === 'idle' ? 'pending' : status
  return {
    definitionId: 'sage.main', definitionVersion: 1, definitionMissing: false,
    runId: status === 'idle' ? '' : 'run-1', status, activeStageId: null,
    stages: [{ id: 'act', label: '执行任务', status: stageStatus, visitCount: 1, lastSequence: 4, detail: '工具返回失败' }],
    transitions: [], visitedPath: ['act'], lastSequence: status === 'idle' ? 0 : 4,
  }
}

function goal(): CodingThreadGoal {
  return {
    goal_id: 'goal-1', revision: 1, description: '验证恢复路径', completion_criteria: ['完成真实演练'],
    status: 'blocked', evaluation: {
      status: 'blocked', blocker: 'run_failed', evidence_refs: [], next_action: '恢复最后稳定运行',
      source_run_id: 'run-1', evaluated_at: '2026-07-22T00:00:00Z',
      criteria: [{ index: 0, status: 'blocked', evidence_refs: [] }],
    },
    continuation: { mode: 'manual', max_auto_followups: 0, auto_followups_started: 0, no_progress_streak: 0, last_progress_fingerprint: '', stop_reason: null },
    created_at: '', updated_at: '',
  }
}

const context: HarnessSurfaceContext = {
  surface: 'knowledge', workspaceId: 'knowledge-local',
  resource: { type: 'knowledge_page', id: 'page-1', revision: 'rev-1', label: '恢复机制' },
  selection: { type: 'graph_node', id: 'node-1', revision: 'node-rev-1', label: '恢复机制' },
  graphRevision: 'graph-1', operationRefs: [],
}

it('orders decision facts before run, evidence, and context', () => {
  const review = emptyHarnessReviewBundle('run-1')
  review.evidence = {
    status: 'ready', query: '恢复', omittedCount: 0,
    items: [{ id: 'e-1', title: 'Checkpoint', source: 'docs', pageRevision: 'rev-1', excerpt: 'evidence' }],
  }
  review.deposit = { status: 'review', proposalId: 'p-1', revision: 1, items: ['记住恢复边界'], source: 'reflection' }
  const wrapper = mount(FactsRail, {
    props: {
      projection: projection(), connectionState: 'connected', threadGoal: goal(),
      reviewBundle: review, sourceProposalCount: 1, context, toolCallCount: 2,
    },
    slots: { attention: '<button type="button">批准工具</button>' },
  })

  expect(wrapper.findAll('.fact-section').map((item) => item.attributes('data-fact'))).toEqual([
    'attention', 'recovery', 'goal', 'run', 'evidence', 'context',
  ])
  expect(wrapper.text()).toContain('批准工具')
  expect(wrapper.text()).toContain('恢复最后稳定运行')
  expect(wrapper.text()).toContain('1 条证据')
  expect(wrapper.text()).toContain('2 条待审阅沉淀')
})

it('does not render empty fact modules', () => {
  const review = emptyHarnessReviewBundle()
  review.practice.status = 'complete'
  const wrapper = mount(FactsRail, {
    props: {
      projection: projection('idle'), connectionState: 'idle', threadGoal: null,
      reviewBundle: review, sourceProposalCount: 0, context: null,
    },
  })

  expect(wrapper.findAll('.fact-section')).toHaveLength(0)
  expect(wrapper.get('.facts-empty').text()).toContain('等待你的下一步')
})
