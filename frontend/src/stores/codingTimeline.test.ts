import { describe, expect, it } from 'vitest'
import type { CodingTimelineEvent } from '../types/api'
import { createTimelineProjection, mergeTimelineEvents } from './codingTimeline'

function event(
  sequence: number,
  kind: CodingTimelineEvent['kind'],
  payload: Record<string, unknown>,
  overrides: Partial<CodingTimelineEvent> = {},
): CodingTimelineEvent {
  return {
    event_id: `event-${sequence}`,
    session_id: 'session-a',
    run_id: 'run-1',
    sequence,
    kind,
    status: 'completed',
    timestamp: `2026-07-12T00:00:${String(sequence).padStart(2, '0')}Z`,
    payload,
    ...overrides,
  }
}

describe('coding timeline projection', () => {
  it('deduplicates event ids and keeps monotonic sequence order', () => {
    const merged = mergeTimelineEvents(
      [event(2, 'assistant', { type: 'text_delta', delta: '好' })],
      [event(1, 'user', { type: 'user', content: '你好' }), event(2, 'assistant', { type: 'text_delta', delta: '重复' })],
    )

    expect(merged.map((item) => item.event_id)).toEqual(['event-1', 'event-2'])
    expect(merged[1].payload.delta).toBe('好')
  })

  it('projects stable turns and combines assistant deltas before final', () => {
    const projection = createTimelineProjection([
      event(1, 'run', { event: 'run_started' }, { status: 'running' }),
      event(2, 'user', { type: 'user', content: '检查项目' }),
      event(3, 'assistant', { type: 'text_delta', delta: '正在' }),
      event(4, 'assistant', { type: 'text_delta', delta: '检查' }),
      event(5, 'assistant', { type: 'final', content: '检查完成' }),
    ])

    expect(projection.turns).toHaveLength(1)
    expect(projection.turns[0]).toMatchObject({
      id: 'turn:run-1',
      run_id: 'run-1',
      user: { content: '检查项目' },
      assistant: { content: '检查完成', streaming: false },
    })
  })

  it('settles each requested model activity when its response is parsed', () => {
    const projection = createTimelineProjection([
      event(1, 'model', { type: 'model_requested' }, { status: 'running' }),
      event(2, 'model', { type: 'model_parsed', kind: 'tool' }),
      event(3, 'model', { type: 'model_requested' }, { status: 'running' }),
      event(4, 'model', { type: 'model_parsed', kind: 'retry' }),
    ])

    expect(projection.turns[0].model).toEqual([
      expect.objectContaining({ type: 'model_requested', status: 'completed' }),
      expect.objectContaining({ type: 'model_parsed', status: 'completed' }),
      expect.objectContaining({ type: 'model_requested', status: 'error' }),
      expect.objectContaining({ type: 'model_parsed', status: 'completed' }),
    ])
  })

  it('retains tool arguments, results, errors and approval ownership', () => {
    const projection = createTimelineProjection([
      event(1, 'user', { type: 'user', content: '改文件' }),
      event(2, 'tool', { type: 'tool_call', tool: 'patch_file', args: { path: 'app.py' } }, { status: 'running' }),
      event(3, 'approval', {
        type: 'approval_required', approval_id: 'approval-1', tool: 'patch_file',
        args: { path: 'app.py' }, description: '需要确认', pattern_key: 'tool:patch_file',
      }, { status: 'blocked' }),
      event(4, 'tool', {
        type: 'tool_result', tool: 'patch_file', args: { path: 'app.py' },
        content: '被策略拒绝', is_error: true, policy_reason: 'denied',
      }, { status: 'error' }),
    ])

    expect(projection.turns[0].tools[0]).toMatchObject({
      tool: 'patch_file',
      args: { path: 'app.py' },
      result: '被策略拒绝',
      is_error: true,
      status: 'error',
    })
    expect(projection.turns[0].approvals[0]).toMatchObject({
      approval_id: 'approval-1',
      tool: 'patch_file',
      status: 'error',
    })
  })

  it('settles the original approval when approval_granted arrives', () => {
    const projection = createTimelineProjection([
      event(1, 'approval', {
        type: 'approval_required', approval_id: 'approval-1', tool: 'write_file',
        args: { path: 'a.txt' }, description: '需要确认', pattern_key: 'tool:write_file',
      }, { status: 'blocked' }),
      event(2, 'approval', { type: 'approval_granted', tool: 'write_file' }),
    ])

    expect(projection.turns[0].approvals).toHaveLength(1)
    expect(projection.turns[0].approvals[0].status).toBe('completed')
  })

  it('matches repeated same-name tool results by args and preserves call event ids', () => {
    const projection = createTimelineProjection([
      event(1, 'tool', { type: 'tool_call', tool: 'read_file', args: { path: 'a.txt' } }, { status: 'running' }),
      event(2, 'tool', { type: 'tool_call', tool: 'read_file', args: { path: 'b.txt' } }, { status: 'running' }),
      event(3, 'tool', {
        type: 'tool_result', tool: 'read_file', args: { path: 'a.txt' }, content: 'A', is_error: false,
      }),
      event(4, 'tool', {
        type: 'tool_result', tool: 'read_file', args: { path: 'b.txt' }, content: 'B', is_error: false,
      }),
    ])

    expect(projection.turns[0].tools).toEqual([
      expect.objectContaining({ id: 'event-1', args: { path: 'a.txt' }, result: 'A' }),
      expect.objectContaining({ id: 'event-2', args: { path: 'b.txt' }, result: 'B' }),
    ])
  })

  it('keeps context, memory and agent events on their run turn', () => {
    const projection = createTimelineProjection([
      event(1, 'context', { type: 'context_usage_updated', used_tokens: 42 }),
      event(2, 'memory', { type: 'memory_proposal_ready', proposal_id: 'memory-1' }),
      event(3, 'agent', { type: 'agent_started', profile: 'explore', agent_run_id: 'agent-1' }, { status: 'running' }),
    ])

    expect(projection.turns[0].context[0].payload.used_tokens).toBe(42)
    expect(projection.turns[0].memory[0].payload.proposal_id).toBe('memory-1')
    expect(projection.turns[0].agents[0]).toMatchObject({ type: 'agent_started', status: 'running' })
  })

  it('keeps exactly one terminal per run even when input contains duplicates', () => {
    const terminal = event(2, 'terminal', { event: 'run_completed' }, { status: 'completed' })
    const projection = createTimelineProjection([
      event(1, 'user', { type: 'user', content: '完成' }),
      terminal,
      { ...terminal },
    ])

    expect(projection.turns[0].terminal).toMatchObject({ event_id: 'event-2' })
    expect(projection.terminals).toHaveLength(1)
  })

  it.each([
    ['step_limit', '已达到步骤上限'],
    ['cancelled', '已停止当前运行'],
  ])('projects system %s content as the assistant response', (type, content) => {
    const projection = createTimelineProjection([
      event(1, 'user', { type: 'user', content: '执行' }),
      event(2, 'system', { type, content }),
    ])

    expect(projection.turns[0].assistant).toMatchObject({ content, streaming: false })
  })

  it.each(['cancelled', 'interrupted', 'error'] as const)(
    'settles open tool and approval rows when a run is %s',
    (terminalStatus) => {
      const projection = createTimelineProjection([
        event(0, 'model', { type: 'model_requested' }, { status: 'running' }),
        event(1, 'tool', { type: 'tool_call', tool: 'write_file', args: { path: 'a.txt' } }, { status: 'running' }),
        event(2, 'approval', {
          type: 'approval_required', approval_id: 'approval-1', tool: 'write_file',
          args: { path: 'a.txt' }, description: '确认', pattern_key: 'tool:write_file',
        }, { status: 'blocked' }),
        event(3, 'terminal', { event: `run_${terminalStatus}` }, { status: terminalStatus }),
      ])

      expect(projection.turns[0].model[0].status).toBe(terminalStatus)
      expect(projection.turns[0].tools[0].status).toBe(terminalStatus)
      expect(projection.turns[0].approvals[0].status).toBe(terminalStatus)
    },
  )
})
