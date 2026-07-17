import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { HarnessProjection } from '../../harness/types'
import HarnessRunStatus from './HarnessRunStatus.vue'

function projection(overrides: Partial<HarnessProjection> = {}): HarnessProjection {
  return {
    definitionId: 'sage.coding.practice',
    definitionVersion: 1,
    definitionMissing: false,
    runId: 'run-a',
    status: 'running',
    activeStageId: 'act',
    stages: [
      { id: 'plan', label: '规划', status: 'completed', visitCount: 2, lastSequence: 2, durationMs: 1_200 },
      {
        id: 'act', label: '调用工具', status: 'running', visitCount: 1, lastSequence: 3,
        detail: 'run_shell · npm test', operationRef: { kind: 'coding_run', id: 'run-a' },
      },
    ],
    transitions: [
      { id: 'plan-act', from: 'plan', to: 'act', takenCount: 1, lastSequence: 3, active: true },
    ],
    visitedPath: ['plan', 'act'],
    lastSequence: 3,
    ...overrides,
  }
}

describe('HarnessRunStatus', () => {
  it('renders the active path, repeated visits, duration and tool summary', () => {
    const wrapper = mount(HarnessRunStatus, { props: { projection: projection() } })

    expect(wrapper.get('[aria-current="step"]').text()).toContain('调用工具')
    expect(wrapper.get('[data-stage-id="plan"]').text()).toContain('1.2s')
    expect(wrapper.get('[data-stage-id="plan"]').text()).toContain('2')
    expect(wrapper.get('[data-stage-id="act"]').text()).toContain('run_shell · npm test')
    expect(wrapper.get('.stage-edge').classes()).toContain('taken')
  })

  it('renders sanitized runtime resources without connection configuration', () => {
    const wrapper = mount(HarnessRunStatus, {
      props: {
        projection: projection({
          runtimeResources: [
            {
              id: 'mcp-catalog', kind: 'mcp', label: 'MCP 目录',
              detail: '3 个服务 · 2 已配置 · 1 未配置', status: 'blocked',
            },
            {
              id: 'context-budget', kind: 'context', label: '上下文',
              detail: '420 / 32000 tokens', status: 'completed',
            },
          ],
        }),
      },
    })

    expect(wrapper.get('.runtime-resources').text()).toContain('MCP 目录')
    expect(wrapper.get('.runtime-resources').text()).toContain('1 未配置')
    expect(wrapper.get('.runtime-resources').text()).toContain('420 / 32000 tokens')
    expect(wrapper.text()).not.toContain('command')
    expect(wrapper.text()).not.toContain('env')
  })

  it('shows an ordered replay notice when the historical definition is missing', () => {
    const wrapper = mount(HarnessRunStatus, {
      props: {
        projection: projection({
          definitionId: 'legacy.flow',
          definitionVersion: 7,
          definitionMissing: true,
        }),
      },
    })

    expect(wrapper.get('.definition-fallback').text()).toContain('legacy.flow v7')
  })

  it('does not repeat an internal coding run id in the visible stage copy', () => {
    const wrapper = mount(HarnessRunStatus, {
      props: {
        projection: projection({
          stages: [{
            id: 'context', label: '组装上下文', status: 'completed',
            visitCount: 1, lastSequence: 2,
            operationRef: { kind: 'coding_run', id: 'run-internal' },
          }],
        }),
      },
    })

    expect(wrapper.text()).not.toContain('run-internal')
  })
})
