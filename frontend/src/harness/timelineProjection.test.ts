import { describe, expect, it } from 'vitest'
import type { CodingTimelineEvent } from '../types/api'
import { adaptCodingTimeline, codingHarnessDefinition, projectLatestCodingHarness } from './surfaces/coding'
import { mergeHarnessEvents, projectHarnessTimeline } from './timelineProjection'
import type { HarnessTimelineEvent } from './types'

function harnessEvent(
  sequence: number,
  type: HarnessTimelineEvent['type'],
  overrides: Partial<HarnessTimelineEvent> = {},
): HarnessTimelineEvent {
  return {
    eventId: `h-${sequence}`,
    runId: 'run-a',
    sequence,
    timestamp: `2026-07-16T00:00:${String(sequence).padStart(2, '0')}Z`,
    type,
    definitionId: codingHarnessDefinition.id,
    definitionVersion: codingHarnessDefinition.version,
    ...overrides,
  }
}

function codingEvent(
  sequence: number,
  kind: CodingTimelineEvent['kind'],
  payload: Record<string, unknown>,
  status: CodingTimelineEvent['status'] = 'completed',
): CodingTimelineEvent {
  return {
    event_id: `coding-${sequence}`,
    session_id: 'session-a',
    run_id: 'run-a',
    sequence,
    kind,
    status,
    timestamp: `2026-07-16T00:00:${String(sequence).padStart(2, '0')}Z`,
    payload,
  }
}

describe('harness timeline projection', () => {
  it('deduplicates events and keeps deterministic sequence order', () => {
    const merged = mergeHarnessEvents(
      [harnessEvent(2, 'stage_started', { stageId: 'plan' })],
      [harnessEvent(1, 'stage_completed', { stageId: 'receive' }), harnessEvent(2, 'stage_failed', { stageId: 'plan' })],
    )

    expect(merged.map((event) => event.eventId)).toEqual(['h-1', 'h-2'])
    expect(merged[1].type).toBe('stage_started')
  })

  it('projects visited stages, transitions, durations and terminal status', () => {
    const projection = projectHarnessTimeline([
      harnessEvent(1, 'stage_started', { stageId: 'receive' }),
      harnessEvent(2, 'stage_completed', { stageId: 'receive' }),
      harnessEvent(3, 'transition_taken', { fromStageId: 'receive', toStageId: 'context' }),
      harnessEvent(4, 'stage_started', { stageId: 'context' }),
      harnessEvent(5, 'stage_completed', { stageId: 'context' }),
      harnessEvent(6, 'run_terminal', { status: 'completed' }),
    ], codingHarnessDefinition)

    expect(projection.status).toBe('completed')
    expect(projection.activeStageId).toBeNull()
    expect(projection.visitedPath).toEqual(['receive', 'context'])
    expect(projection.stages.find((stage) => stage.id === 'receive')).toMatchObject({
      status: 'completed',
      visitCount: 1,
      durationMs: 1_000,
    })
    expect(projection.transitions.find((edge) => edge.id === 'receive-context')?.takenCount).toBe(1)
  })

  it('falls back to an ordered stage list when the recorded definition is unavailable', () => {
    const projection = projectHarnessTimeline([
      harnessEvent(1, 'stage_started', { stageId: 'custom_gate', definitionVersion: 99 }),
      harnessEvent(2, 'stage_completed', { stageId: 'custom_gate', definitionVersion: 99 }),
    ], codingHarnessDefinition)

    expect(projection.definitionMissing).toBe(true)
    expect(projection.stages).toEqual([
      expect.objectContaining({ id: 'custom_gate', label: 'Custom Gate', status: 'completed' }),
    ])
  })

  it('adapts the existing Coding loop without counting every text delta as a new visit', () => {
    const events = adaptCodingTimeline([
      codingEvent(1, 'user', { type: 'user', content: '检查项目' }),
      codingEvent(2, 'model', { type: 'model_requested' }, 'running'),
      codingEvent(3, 'model', { type: 'model_parsed', kind: 'tool' }),
      codingEvent(4, 'tool', { type: 'tool_call', tool: 'run_shell', args: { command: 'npm test' } }, 'running'),
      codingEvent(5, 'tool', { type: 'tool_result', tool: 'run_shell', args: { command: 'npm test' }, content: 'ok' }),
      codingEvent(6, 'model', { type: 'model_requested' }, 'running'),
      codingEvent(7, 'model', { type: 'model_parsed', kind: 'final' }),
      codingEvent(8, 'assistant', { type: 'text_delta', delta: '完' }, 'running'),
      codingEvent(9, 'assistant', { type: 'text_delta', delta: '成' }, 'running'),
      codingEvent(10, 'assistant', { type: 'final', content: '完成' }),
      codingEvent(11, 'terminal', { type: 'run_finished' }),
    ])
    const projection = projectHarnessTimeline(events, codingHarnessDefinition)

    expect(projection.status).toBe('completed')
    expect(projection.stages.find((stage) => stage.id === 'act')).toMatchObject({
      status: 'completed',
      detail: 'run_shell · npm test',
      operationRef: { kind: 'coding_run', id: 'run-a' },
    })
    expect(projection.stages.find((stage) => stage.id === 'reply')?.visitCount).toBe(1)
    expect(projection.visitedPath).toEqual(['receive', 'context', 'plan', 'act', 'plan', 'reply'])
  })

  it('produces the same projection from live batches and a full replay', () => {
    const source = [
      codingEvent(1, 'user', { type: 'user', content: '解释项目' }),
      codingEvent(2, 'model', { type: 'model_requested' }, 'running'),
      codingEvent(3, 'model', { type: 'model_parsed', kind: 'final' }),
      codingEvent(4, 'assistant', { type: 'final', content: '完成' }),
      codingEvent(5, 'terminal', { type: 'run_finished' }),
    ]
    const adapted = adaptCodingTimeline(source)
    let liveEvents: HarnessTimelineEvent[] = []
    for (const event of adapted) liveEvents = mergeHarnessEvents(liveEvents, [event])

    expect(projectHarnessTimeline(liveEvents, codingHarnessDefinition)).toEqual(
      projectHarnessTimeline(adapted, codingHarnessDefinition),
    )
  })

  it('prefers explicit stage facts without duplicating legacy inference', () => {
    const events = adaptCodingTimeline([
      codingEvent(1, 'user', { type: 'user', content: '解释项目' }),
      codingEvent(2, 'harness', {
        type: 'stage_started',
        definition_id: 'sage.coding.practice',
        definition_version: 1,
        stage_id: 'receive',
      }, 'running'),
      codingEvent(3, 'harness', {
        type: 'stage_completed',
        definition_id: 'sage.coding.practice',
        definition_version: 1,
        stage_id: 'receive',
      }),
      codingEvent(4, 'terminal', { event: 'run_completed' }),
    ])

    const projection = projectHarnessTimeline(events, codingHarnessDefinition)
    expect(projection.visitedPath).toEqual(['receive'])
    expect(projection.stages.find((stage) => stage.id === 'receive')?.visitCount).toBe(1)
  })

  it('selects the active run while retaining deterministic replay output', () => {
    const source = [
      codingEvent(1, 'user', { type: 'user', content: 'old' }),
      { ...codingEvent(2, 'terminal', { event: 'run_completed' }), run_id: 'run-old' },
      { ...codingEvent(3, 'harness', {
        type: 'stage_started',
        definition_id: 'sage.coding.practice',
        definition_version: 1,
        stage_id: 'act',
        operation_ref: { kind: 'coding_run', id: 'run-live' },
      }, 'blocked'), run_id: 'run-live' },
    ]

    const projection = projectLatestCodingHarness(source, 'run-live')
    expect(projection.runId).toBe('run-live')
    expect(projection.activeStageId).toBe('act')
    expect(projection.status).toBe('blocked')
    expect(projection.stages.find((stage) => stage.id === 'act')?.operationRef).toEqual({
      kind: 'coding_run', id: 'run-live',
    })
  })

  it('projects DeerFlow MCP context into the stage path and sanitized resources', () => {
    const projection = projectLatestCodingHarness([
      codingEvent(1, 'user', { type: 'user', content: '列出文件' }),
      codingEvent(2, 'harness', {
        type: 'mcp_catalog_updated',
        servers: [
          { name: 'docs', transport: 'stdio', status: 'configured', tool_names: [] },
          { name: 'remote', transport: 'http', status: 'unconfigured', tool_names: [] },
        ],
      }),
      codingEvent(3, 'tool', {
        type: 'tool_call', tool: 'run_shell', args: { command: 'pwd' },
      }, 'running'),
      codingEvent(4, 'tool', {
        type: 'tool_result', tool: 'run_shell', args: { command: 'pwd' }, content: '/repo',
      }),
      codingEvent(5, 'assistant', { type: 'text_delta', delta: '完成' }, 'running'),
    ])

    expect(projection.visitedPath).toEqual(['receive', 'context', 'plan', 'act', 'reply'])
    expect(projection.stages.find((stage) => stage.id === 'act')?.detail).toBe('run_shell · pwd')
    expect(projection.runtimeResources).toContainEqual(expect.objectContaining({
      kind: 'mcp', label: 'MCP 目录', detail: '2 个服务 · 1 已配置 · 1 未配置',
      status: 'blocked',
    }))
  })

  it('updates one child resource from started to terminal status', () => {
    const projection = projectLatestCodingHarness([
      codingEvent(1, 'agent', {
        type: 'subagent_started', child_run_id: 'child-1', description: '读取项目',
      }, 'running'),
      codingEvent(2, 'agent', {
        type: 'subagent_completed', child_run_id: 'child-1', result_ref: 'subagent://child-1',
      }),
    ])

    expect(projection.runtimeResources).toContainEqual({
      id: 'agent:child-1', kind: 'agent', label: '子代理', detail: '读取项目', status: 'completed',
    })
  })

  it('keeps blocked and resumed approval activity in one tool-stage visit', () => {
    const events = adaptCodingTimeline([
      codingEvent(1, 'harness', {
        type: 'stage_started',
        definition_id: 'sage.coding.practice',
        definition_version: 1,
        stage_id: 'act',
        detail: 'run_shell · pwd',
      }, 'blocked'),
      codingEvent(2, 'harness', {
        type: 'stage_started',
        definition_id: 'sage.coding.practice',
        definition_version: 1,
        stage_id: 'act',
        detail: 'run_shell · pwd',
      }, 'running'),
    ])

    const projection = projectHarnessTimeline(events, codingHarnessDefinition)
    expect(projection.status).toBe('running')
    expect(projection.activeStageId).toBe('act')
    expect(projection.visitedPath).toEqual(['act'])
    expect(projection.stages.find((stage) => stage.id === 'act')).toMatchObject({
      status: 'running',
      visitCount: 1,
      detail: 'run_shell · pwd',
    })
  })
})
