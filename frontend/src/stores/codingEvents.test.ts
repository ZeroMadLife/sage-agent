import { describe, expect, it } from 'vitest'
import { ref } from 'vue'
import { applyCodingEvent, type ChatMessage } from './codingEvents'
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
})
