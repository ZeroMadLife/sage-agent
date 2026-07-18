import { describe, expect, it } from 'vitest'
import type { HarnessProjection } from './types'
import { harnessRunVisualState } from './runVisualState'

function projection(
  status: HarnessProjection['status'],
  activeStageId: string | null = null,
): HarnessProjection {
  return {
    definitionId: 'sage.test',
    definitionVersion: 1,
    definitionMissing: false,
    runId: status === 'idle' ? '' : 'run-1',
    status,
    activeStageId,
    stages: activeStageId ? [{
      id: activeStageId,
      label: activeStageId,
      status: 'running',
      visitCount: 1,
      lastSequence: 1,
    }] : [],
    transitions: [],
    visitedPath: [],
    lastSequence: 0,
  }
}

describe('harnessRunVisualState', () => {
  it('derives every visible state from timeline and transport facts', () => {
    expect(harnessRunVisualState(projection('idle'), 'connected')).toBe('idle')
    expect(harnessRunVisualState(projection('running', 'plan'), 'connected')).toBe('running')
    expect(harnessRunVisualState(projection('running', 'act'), 'connected')).toBe('tool')
    expect(harnessRunVisualState(projection('blocked', 'act'), 'connected')).toBe('approval')
    expect(harnessRunVisualState(projection('failed'), 'connected')).toBe('failed')
    expect(harnessRunVisualState(projection('completed'), 'connected')).toBe('completed')
    expect(harnessRunVisualState(projection('running', 'act'), 'recovering')).toBe('recovering')
  })
})
