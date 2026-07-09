import { describe, expect, it } from 'vitest'
import { ref } from 'vue'
import { applyCodingEvent, type ChatMessage, type PlanReviewState } from './codingEvents'
import type { CodingApproval } from '../types/api'

function state() {
  return {
    sessionId: ref('coding_1'),
    messages: ref<ChatMessage[]>([]),
    isThinking: ref(false),
    errorMessage: ref(''),
    contextChars: ref(0),
    pendingApproval: ref<CodingApproval | null>(null),
    thinkingPhase: ref(''),
    runtimeMode: ref('default'),
    planTopic: ref(''),
    planPath: ref(''),
    planReview: ref<PlanReviewState | null>(null),
  }
}

describe('codingEvents', () => {
  it('appends tool activity on tool_call', () => {
    const current = state()
    current.messages.value = [{ role: 'assistant', content: '', tools: [], isThinking: true }]

    applyCodingEvent(current, {
      type: 'tool_call',
      tool: 'read_file',
      args: { path: 'README.md' },
    })

    expect(current.messages.value[0].tools).toHaveLength(1)
    expect(current.messages.value[0].tools![0].status).toBe('running')
  })

  it('updates the latest running tool on tool_result', () => {
    const current = state()
    current.messages.value = [
      {
        role: 'assistant',
        content: '',
        tools: [{ tool: 'read_file', args: {}, status: 'running', content: '' }],
        isThinking: true,
      },
    ]

    const effect = applyCodingEvent(current, {
      type: 'tool_result',
      tool: 'read_file',
      args: {},
      content: '# Sage',
      is_error: false,
    })

    expect(effect.toolResult?.tool).toBe('read_file')
    expect(current.messages.value[0].tools![0].status).toBe('done')
    expect(current.messages.value[0].tools![0].content).toBe('# Sage')
  })

  it('finalizes assistant thinking message on terminal events', () => {
    const current = state()
    current.messages.value = [{ role: 'assistant', content: '', tools: [], isThinking: true }]
    current.isThinking.value = true

    const effect = applyCodingEvent(current, { type: 'final', content: '完成' })

    expect(effect.terminal).toBe(true)
    expect(current.messages.value[0].content).toBe('完成')
    expect(current.messages.value[0].isThinking).toBe(false)
    expect(current.isThinking.value).toBe(false)
  })

  it('sets pending approval from approval_required', () => {
    const current = state()

    const effect = applyCodingEvent(current, {
      type: 'approval_required',
      approval_id: 'appr_1',
      tool: 'write_file',
      args: { path: 'README.md' },
      description: 'write_file requires approval.',
      pattern_key: 'tool:write_file',
    })

    expect(effect.approvalRequired).toBe(true)
    expect(current.pendingApproval.value?.approval_id).toBe('appr_1')
    expect(current.pendingApproval.value?.session_id).toBe('coding_1')
  })

  it('shows approval-required tools as visible running activity', () => {
    const current = state()
    current.isThinking.value = true

    applyCodingEvent(current, {
      type: 'approval_required',
      approval_id: 'appr_1',
      tool: 'write_file',
      args: { path: 'README.md' },
      description: 'write_file requires approval.',
      pattern_key: 'tool:write_file',
    })

    expect(current.messages.value).toHaveLength(1)
    expect(current.messages.value[0].tools).toEqual([
      {
        tool: 'write_file',
        args: { path: 'README.md' },
        status: 'running',
        content: '等待确认: write_file requires approval.',
      },
    ])
    expect(current.thinkingPhase.value).toBe('等待工具确认...')
  })

  it('reuses approval activity when the approved tool starts running', () => {
    const current = state()
    current.messages.value = [
      {
        role: 'assistant',
        content: '',
        tools: [
          {
            tool: 'write_file',
            args: { path: 'README.md' },
            status: 'running',
            content: '等待确认: write_file requires approval.',
          },
        ],
        isThinking: true,
      },
    ]

    applyCodingEvent(current, {
      type: 'tool_call',
      tool: 'write_file',
      args: { path: 'README.md' },
    })

    expect(current.messages.value[0].tools).toHaveLength(1)
    expect(current.messages.value[0].tools![0].content).toBe('')
  })

  it('keeps denied or policy-blocked tool results visible without a prior tool_call', () => {
    const current = state()
    current.isThinking.value = true

    applyCodingEvent(current, {
      type: 'tool_result',
      tool: 'patch_file',
      args: { path: 'app.py' },
      content: 'Tool blocked by policy.',
      is_error: true,
      policy_reason: 'prior_read_required',
    })

    expect(current.messages.value).toHaveLength(1)
    expect(current.messages.value[0].tools).toEqual([
      {
        tool: 'patch_file',
        args: { path: 'app.py' },
        status: 'error',
        content: 'Tool blocked by policy.',
      },
    ])
  })

  it('tracks thinking phase across trace events', () => {
    const current = state()
    current.isThinking.value = true

    applyCodingEvent(current, { type: 'turn_started' })
    expect(current.thinkingPhase.value).toBe('思考中')

    applyCodingEvent(current, { type: 'model_requested', prompt_chars: 1234 })
    expect(current.thinkingPhase.value).toBe('正在请求模型...')
    expect(current.contextChars.value).toBe(1234)

    applyCodingEvent(current, { type: 'model_parsed', kind: 'tool' })
    expect(current.thinkingPhase.value).toBe('正在执行工具...')

    applyCodingEvent(current, { type: 'model_parsed', kind: 'retry' })
    expect(current.thinkingPhase.value).toBe('模型响应异常,正在重试...')

    applyCodingEvent(current, { type: 'retry', content: 'bad output' })
    expect(current.thinkingPhase.value).toBe('正在重试...')
  })

  it('clears thinking phase on terminal events', () => {
    const current = state()
    current.isThinking.value = true
    current.thinkingPhase.value = '正在请求模型...'
    current.messages.value = [{ role: 'assistant', content: '', tools: [], isThinking: true }]

    applyCodingEvent(current, { type: 'final', content: '完成' })

    expect(current.isThinking.value).toBe(false)
    expect(current.thinkingPhase.value).toBe('')
  })

  it('accumulates text deltas into assistant message', () => {
    const current = state()
    current.isThinking.value = true

    applyCodingEvent(current, { type: 'text_delta', delta: 'Hello' })
    applyCodingEvent(current, { type: 'text_delta', delta: ', ' })
    applyCodingEvent(current, { type: 'text_delta', delta: 'world!' })

    expect(current.messages.value).toHaveLength(1)
    expect(current.messages.value[0].role).toBe('assistant')
    expect(current.messages.value[0].isThinking).toBe(true)
    expect(current.messages.value[0].content).toBe('Hello, world!')
  })

  it('accumulates text deltas into an existing thinking message', () => {
    const current = state()
    current.isThinking.value = true
    current.messages.value = [
      { role: 'assistant', content: 'partial', tools: [], isThinking: true },
    ]

    applyCodingEvent(current, { type: 'text_delta', delta: ' response' })

    expect(current.messages.value).toHaveLength(1)
    expect(current.messages.value[0].content).toBe('partial response')
    expect(current.messages.value[0].isThinking).toBe(true)
  })

  it('final event overwrites accumulated content', () => {
    const current = state()
    current.isThinking.value = true

    applyCodingEvent(current, { type: 'text_delta', delta: 'streaming' })
    applyCodingEvent(current, { type: 'text_delta', delta: ' partial' })

    expect(current.messages.value[0].content).toBe('streaming partial')

    const effect = applyCodingEvent(current, { type: 'final', content: '完整的最终回复。' })

    expect(effect.terminal).toBe(true)
    expect(current.messages.value).toHaveLength(1)
    expect(current.messages.value[0].content).toBe('完整的最终回复。')
    expect(current.messages.value[0].isThinking).toBe(false)
  })

  it('tool_call clears accumulated streaming text', () => {
    const current = state()
    current.isThinking.value = true

    applyCodingEvent(current, { type: 'text_delta', delta: '我来读取文件' })
    applyCodingEvent(current, { type: 'text_delta', delta: '的内容' })

    expect(current.messages.value).toHaveLength(1)
    expect(current.messages.value[0].content).toBe('我来读取文件的内容')

    applyCodingEvent(current, {
      type: 'tool_call',
      tool: 'read_file',
      args: { path: 'README.md' },
    })

    expect(current.messages.value).toHaveLength(1)
    expect(current.messages.value[0].content).toBe('')
    expect(current.messages.value[0].tools).toHaveLength(1)
    expect(current.messages.value[0].tools![0].tool).toBe('read_file')
    expect(current.messages.value[0].tools![0].status).toBe('running')
  })

  it('enters plan mode on runtime_mode_changed', () => {
    const current = state()

    applyCodingEvent(current, {
      type: 'runtime_mode_changed',
      run_id: 'run_xxx',
      mode: 'plan',
      topic: 'refactor auth module',
      plan_path: '.coding/plans/xxx-plan.md',
    })

    expect(current.runtimeMode.value).toBe('plan')
    expect(current.planTopic.value).toBe('refactor auth module')
    expect(current.planPath.value).toBe('.coding/plans/xxx-plan.md')
  })

  it('exits plan mode back to default on runtime_mode_changed', () => {
    const current = state()
    current.runtimeMode.value = 'plan'
    current.planTopic.value = 'refactor auth module'
    current.planPath.value = '.coding/plans/xxx-plan.md'

    applyCodingEvent(current, { type: 'runtime_mode_changed', mode: 'default' })

    expect(current.runtimeMode.value).toBe('default')
    expect(current.planTopic.value).toBe('')
    expect(current.planPath.value).toBe('')
  })

  it('handles runtime_mode_changed with missing optional fields', () => {
    const current = state()
    current.runtimeMode.value = 'plan'
    current.planTopic.value = 'topic'
    current.planPath.value = 'path'

    applyCodingEvent(current, { type: 'runtime_mode_changed', mode: 'default' })

    expect(current.runtimeMode.value).toBe('default')
    expect(current.planTopic.value).toBe('')
    expect(current.planPath.value).toBe('')
  })

  it('stores plan review from plan_ready_for_review', () => {
    const current = state()

    applyCodingEvent(current, {
      type: 'plan_ready_for_review',
      run_id: 'run_xxx',
      review_id: 'plan_review_1',
      plan_path: '.coding/plans/xxx-plan.md',
      summary: '# Plan\n\n- step 1',
    })

    expect(current.planReview.value).toEqual({
      review_id: 'plan_review_1',
      plan_path: '.coding/plans/xxx-plan.md',
      summary: '# Plan\n\n- step 1',
    })
  })

  it('clears plan review when runtime_mode_changed exits to default', () => {
    const current = state()
    current.runtimeMode.value = 'plan'
    current.planReview.value = {
      review_id: 'plan_review_1',
      plan_path: '.coding/plans/xxx-plan.md',
      summary: '# Plan',
    }

    applyCodingEvent(current, { type: 'runtime_mode_changed', mode: 'default' })

    expect(current.runtimeMode.value).toBe('default')
    expect(current.planReview.value).toBeNull()
  })

  it('keeps plan review when runtime_mode_changed stays in plan mode', () => {
    const current = state()
    current.runtimeMode.value = 'plan'
    current.planReview.value = {
      review_id: 'plan_review_1',
      plan_path: '.coding/plans/xxx-plan.md',
      summary: '# Plan',
    }

    applyCodingEvent(current, {
      type: 'runtime_mode_changed',
      mode: 'plan',
      topic: 'updated topic',
      plan_path: '.coding/plans/xxx-plan.md',
    })

    expect(current.runtimeMode.value).toBe('plan')
    expect(current.planReview.value).not.toBeNull()
  })
})
