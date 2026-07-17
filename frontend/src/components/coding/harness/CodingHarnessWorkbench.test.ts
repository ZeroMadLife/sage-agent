import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { HarnessProjection } from '../../../harness/types'
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
    }],
  }
}

describe('CodingHarnessWorkbench', () => {
  it('summarizes the selected run without inventing runtime data', () => {
    const wrapper = mount(CodingHarnessWorkbench, {
      props: { projection: projection(), sessionTitle: '重构项目', toolCallCount: 3 },
    })

    expect(wrapper.attributes('data-run-id')).toBe('run-a')
    expect(wrapper.get('.workbench-title').text()).toContain('Harness 2.0')
    expect(wrapper.get('.workbench-title').text()).toContain('重构项目')
    expect(wrapper.get('.workbench-metrics').text()).toContain('LIVE')
    expect(wrapper.get('.workbench-metrics').text()).toContain('2.5s')
    expect(wrapper.get('.workbench-metrics').text()).toContain('420 / 32000 tokens')
    expect(wrapper.get('.workbench-metrics').text()).toContain('3')
    expect(wrapper.get('[aria-current="step"]').text()).toContain('调用工具')
  })
})
