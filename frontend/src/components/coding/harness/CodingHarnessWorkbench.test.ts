import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { HarnessReviewBundle } from '../../../harness/reviewBundle'
import type { HarnessProjection } from '../../../harness/types'
import type { CodingKnowledgeSourceProposal, CodingThreadGoal } from '../../../types/api'
import CodingHarnessWorkbench from './CodingHarnessWorkbench.vue'

function projection(): HarnessProjection {
  return {
    definitionId: 'sage.coding.practice',
    definitionVersion: 1,
    definitionMissing: false,
    runId: 'run-a',
    status: 'running',
    activeStageId: 'act',
    stages: [
      { id: 'receive', label: '接收目标', status: 'completed', visitCount: 1, lastSequence: 1, durationMs: 500 },
      { id: 'plan', label: '规划', status: 'completed', visitCount: 2, lastSequence: 2, durationMs: 1_200 },
      { id: 'act', label: '调用工具', status: 'running', visitCount: 1, lastSequence: 3, durationMs: 800 },
    ],
    transitions: [],
    visitedPath: ['receive', 'plan', 'act'],
    lastSequence: 3,
    runtimeResources: [{
      id: 'context-budget',
      kind: 'context',
      label: '上下文',
      detail: '420 / 32000 tokens',
      status: 'completed',
    }, {
      id: 'run-budget',
      kind: 'budget',
      label: '本轮预算',
      detail: '24k / 100k tokens · 3/24 模型 · 5/64 工具',
      status: 'completed',
    }],
  }
}

function reviewBundle(): HarnessReviewBundle {
  return {
    runId: 'run-a',
    evidence: {
      status: 'ready', query: 'checkpoint 恢复', omittedCount: 0,
      items: [{
        id: 'evidence-1', title: '持久化机制', source: 'knowledge/wiki.md',
        pageRevision: 'page-rev-18', excerpt: 'checkpoint 保存可恢复状态。',
      }],
    },
    practice: {
      status: 'complete', headline: '恢复练习已通过', toolCount: 3,
      completedToolCount: 3, failedToolCount: 0, approvalCount: 0,
      durationMs: 2_500, changedFiles: ['checkpointing.md'],
    },
    deposit: {
      status: 'review', proposalId: 'proposal-1', revision: 2,
      items: ['把恢复边界写入长期记忆'], source: 'run-a',
    },
  }
}

function sourceProposal(): CodingKnowledgeSourceProposal {
  return {
    proposal_id: 'ksprop_1', thread_id: 'session-1', run_id: 'run-a',
    artifact_ref: 'sage://coding/session-1/run-a/artifacts/fetch', source_kind: 'web',
    canonical_url: 'https://example.com/official', title: '官方证据', media_type: 'text/html',
    retrieved_at: '', content_hash: 'a'.repeat(64), reason: '补齐证据',
    evidence_refs: ['wcite_1'], status: 'pending', revision: 1,
    target_root_id: 'sage-learning', target_relative_path: '', job_id: null,
    last_error: null, decided_by: null, decided_at: null, created_at: '', updated_at: '',
  }
}

function threadGoal(): CodingThreadGoal {
  return {
    goal_id: 'goal-1', revision: 3, description: '掌握 LangGraph checkpoint 恢复',
    completion_criteria: ['解释 thread 与 checkpoint 的差异', '给出官方引用'],
    status: 'active', created_at: '2026-07-20T08:00:00Z', updated_at: '2026-07-20T08:01:00Z',
    evaluation: {
      status: 'continue', blocker: 'goal_not_met_yet', evidence_refs: ['event-1'],
      next_action: '继续补齐官方证据', source_run_id: 'run-a', evaluated_at: '2026-07-20T08:01:00Z',
      criteria: [],
    },
    continuation: {
      mode: 'manual', max_auto_followups: 1, auto_followups_started: 0,
      no_progress_streak: 0, last_progress_fingerprint: '', stop_reason: null,
    },
  }
}

describe('CodingHarnessWorkbench', () => {
  it('projects a running timeline as active learning without inventing mastery', () => {
    const wrapper = mount(CodingHarnessWorkbench, {
      props: { projection: projection(), sessionTitle: '重构项目', toolCallCount: 3 },
    })

    expect(wrapper.attributes('data-run-id')).toBe('run-a')
    expect(wrapper.attributes('data-journey')).toBe('active')
    expect(wrapper.get('.goal-heading').text()).toContain('重构项目')
    expect(wrapper.get('.workbench-metrics').text()).toContain('运行中')
    expect(wrapper.get('.workbench-metrics').text()).toContain('2.5s')
    expect(wrapper.get('.workbench-metrics').text()).toContain('24k / 100k tokens')
    expect(wrapper.get('.workbench-metrics').text()).toContain('3')
    expect(wrapper.get('[aria-current="step"]').text()).toContain('调用工具')
    expect(wrapper.get('.workbench-mark').classes()).toContain('running')
    expect(wrapper.get('.evidence-summary').text()).toContain('尚未评估')
  })

  it('exposes failed and completed states on the workbench chrome', async () => {
    const current = projection()
    const wrapper = mount(CodingHarnessWorkbench, {
      props: { projection: { ...current, status: 'failed' }, sessionTitle: '验证状态' },
    })

    expect(wrapper.get('.workbench-mark').classes()).toContain('failed')
    expect(wrapper.get('.metric-state').text()).toContain('失败')

    await wrapper.setProps({ projection: { ...current, status: 'completed' } })
    expect(wrapper.get('.workbench-mark').classes()).toContain('completed')
    expect(wrapper.get('.metric-state').text()).toContain('已完成')
  })

  it('creates a durable goal contract before the first run', async () => {
    const current = projection()
    const wrapper = mount(CodingHarnessWorkbench, {
      props: {
        projection: {
          ...current, runId: '', status: 'idle', activeStageId: null,
          stages: [], visitedPath: [], lastSequence: 0,
        },
        sessionTitle: '成为独立交付 AI Agent 的工程师',
      },
    })

    expect(wrapper.attributes('data-journey')).toBe('contract')
    expect(wrapper.get('.contract-surface').text()).toContain('目标尚未确认')
    expect(wrapper.get('.contract-surface').text()).not.toContain('42%')
    await wrapper.get('textarea[aria-label="完成标准"]').setValue('完成一次恢复演练\n给出可追溯证据')
    await wrapper.get('.goal-contract-form').trigger('submit')
    expect(wrapper.emitted('saveGoal')).toEqual([[
      '成为独立交付 AI Agent 的工程师',
      ['完成一次恢复演练', '给出可追溯证据'],
    ]])
  })

  it('uses the persisted goal revision and exposes manual continuation', async () => {
    const current = projection()
    const wrapper = mount(CodingHarnessWorkbench, {
      props: {
        projection: {
          ...current, runId: '', status: 'idle', activeStageId: null,
          stages: [], visitedPath: [], lastSequence: 0,
        },
        sessionTitle: '旧会话标题',
        connectionState: 'connected',
        threadGoal: threadGoal(),
      },
    })

    expect(wrapper.attributes('data-journey')).toBe('active')
    expect(wrapper.get('.goal-heading').text()).toContain('掌握 LangGraph checkpoint 恢复')
    expect(wrapper.get('.goal-heading').text()).toContain('rev 3')
    expect(wrapper.get('.evidence-summary').text()).toContain('目标尚未满足')
    await wrapper.get('.goal-actions button.primary').trigger('click')
    expect(wrapper.emitted('continueGoal')).toEqual([[]])
  })

  it('offers Goal setup to a completed legacy Thread without an existing Goal', () => {
    const current = projection()
    const wrapper = mount(CodingHarnessWorkbench, {
      props: {
        projection: { ...current, status: 'completed', activeStageId: null },
        sessionTitle: '已有历史但尚未设置目标',
      },
    })

    expect(wrapper.attributes('data-journey')).toBe('contract')
    expect(
      (wrapper.get('textarea[aria-label="目标结果"]').element as HTMLTextAreaElement).value,
    ).toBe('已有历史但尚未设置目标')
  })

  it('edits an existing Goal through the same revisioned contract', async () => {
    const wrapper = mount(CodingHarnessWorkbench, {
      props: {
        projection: { ...projection(), runId: '', status: 'idle', activeStageId: null },
        sessionTitle: '会话', connectionState: 'connected', threadGoal: threadGoal(),
      },
    })

    await wrapper.get('.goal-actions button').trigger('click')
    await wrapper.get('textarea[aria-label="编辑目标结果"]').setValue('更新后的目标')
    await wrapper.get('.goal-edit-form').trigger('submit')
    expect(wrapper.emitted('saveGoal')?.[0]).toEqual([
      '更新后的目标',
      ['解释 thread 与 checkpoint 的差异', '给出官方引用'],
    ])
  })

  it('shows deposit review only for a real pending proposal', () => {
    const wrapper = mount(CodingHarnessWorkbench, {
      props: {
        projection: { ...projection(), status: 'completed' },
        sessionTitle: '恢复机制研究', reviewBundle: reviewBundle(),
      },
    })

    expect(wrapper.attributes('data-journey')).toBe('review')
    expect(wrapper.get('.review-surface').text()).toContain('确认哪些内容进入长期系统')
    expect(wrapper.get('.review-surface').text()).toContain('1 条可追溯证据')
    expect(wrapper.get('.review-surface').text()).toContain('批准沉淀')
  })

  it('enters deposit review for a real knowledge source proposal', async () => {
    const source = sourceProposal()
    const wrapper = mount(CodingHarnessWorkbench, {
      props: {
        projection: { ...projection(), status: 'completed' },
        sessionTitle: '来源审阅', reviewBundle: {
          ...reviewBundle(), deposit: { status: 'empty', proposalId: '', revision: 0, items: [], source: '' },
        },
        sourceProposals: [source],
      },
    })

    expect(wrapper.attributes('data-journey')).toBe('review')
    expect(wrapper.get('.review-surface').text()).toContain('官方证据')
    await wrapper.get('[data-action="approve-source"]').trigger('click')
    expect(wrapper.emitted('approveSource')).toEqual([[source.proposal_id, source.revision]])
  })

  it('keeps proposal errors next to the review item instead of duplicating them', () => {
    const wrapper = mount(CodingHarnessWorkbench, {
      props: {
        projection: { ...projection(), status: 'completed' },
        sessionTitle: '来源审阅', reviewBundle: {
          ...reviewBundle(), deposit: { status: 'empty', proposalId: '', revision: 0, items: [], source: '' },
        },
        sourceProposals: [sourceProposal()],
        sourceError: '知识来源提案已发生变化，请刷新后重试',
      },
    })

    expect(wrapper.findAll('[role="alert"]')).toHaveLength(1)
  })

  it('prioritizes connection recovery and shows the last sequence', () => {
    const wrapper = mount(CodingHarnessWorkbench, {
      props: {
        projection: projection(), sessionTitle: '恢复机制研究',
        connectionState: 'recovering',
      },
    })

    expect(wrapper.attributes('data-journey')).toBe('recovery')
    expect(wrapper.get('.recovery-surface').text()).toContain('sequence 3')
    expect(wrapper.get('.recovery-surface').text()).toContain('不重新标记已完成工具')
  })

  it('replays repeated stage events instead of collapsing them into one stage row', () => {
    const current = projection()
    const wrapper = mount(CodingHarnessWorkbench, {
      props: {
        projection: {
          ...current,
          stageEvents: [{
            eventId: 'event-1', stageId: 'act', label: '调用工具',
            detail: '第一次检索', status: 'completed', timestamp: '2026-07-18T10:00:00Z', sequence: 4,
          }, {
            eventId: 'event-2', stageId: 'act', label: '调用工具',
            detail: '第二次检索', status: 'running', timestamp: '2026-07-18T10:00:01Z', sequence: 5,
          }],
        },
        sessionTitle: '验证事件回放',
      },
    })

    expect(wrapper.get('.event-replay').text()).toContain('第一次检索')
    expect(wrapper.get('.event-replay').text()).toContain('第二次检索')
  })

  it('opens a traceable child operation from the runtime resource list', async () => {
    const current = projection()
    const wrapper = mount(CodingHarnessWorkbench, {
      props: {
        projection: {
          ...current,
          runtimeResources: [{
            id: 'agent:child-1', kind: 'agent', label: '子代理',
            detail: '比较两份文档', status: 'running',
            operationRef: { kind: 'coding_run', id: 'child-1' },
          }],
        },
        sessionTitle: '验证子代理',
      },
    })

    await wrapper.get('button[aria-label="查看子代理运行详情"]').trigger('click')
    expect(wrapper.emitted('openOperation')).toEqual([[{
      kind: 'coding_run', id: 'child-1',
    }]])
  })
})
