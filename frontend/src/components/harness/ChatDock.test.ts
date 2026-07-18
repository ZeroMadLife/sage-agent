import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { HarnessProjection } from '../../harness/types'
import ChatDock from './ChatDock.vue'

function projection(status: HarnessProjection['status'], activeStageId: string | null) {
  return {
    definitionId: 'sage.test', definitionVersion: 1, definitionMissing: false,
    runId: 'run-1', status, activeStageId,
    stages: [
      { id: 'receive', label: '接收目标', status: 'completed' as const, visitCount: 1, lastSequence: 1 },
      { id: 'act', label: '调用工具', status: status === 'blocked' ? 'blocked' as const : 'running' as const, visitCount: 1, lastSequence: 2 },
    ],
    transitions: [], visitedPath: ['receive', 'act'], lastSequence: 2,
  }
}

describe('ChatDock', () => {
  it('shows tool and frozen context state from supplied facts', () => {
    const wrapper = mount(ChatDock, {
      props: {
        projection: projection('running', 'act'),
        connectionState: 'connected',
        context: {
          surface: 'knowledge', workspaceId: 'knowledge-local',
          resource: { type: 'knowledge_page', id: 'page-1', label: 'Agent Harness' },
          selection: { type: 'graph_node', id: 'node-1', label: 'Agent Harness' },
          graphRevision: 'graph-1', operationRefs: [],
        },
        outputSignature: 'one', messageCount: 0, sessionKey: 'session-1',
        timelineReady: true, isEmpty: true,
      },
    })

    expect(wrapper.attributes('data-run-state')).toBe('tool')
    expect(wrapper.get('.run-strip-title').text()).toContain('调用工具')
    expect(wrapper.get('.surface-context-bar').text()).toContain('Agent Harness')
    expect(wrapper.get('.surface-context-bar').text()).toContain('已冻结')
  })

  it('prioritizes a factual recovery state over the active timeline stage', () => {
    const wrapper = mount(ChatDock, {
      props: {
        projection: projection('running', 'act'), connectionState: 'recovering',
        outputSignature: 'two', messageCount: 1, sessionKey: 'session-1',
        timelineReady: true,
      },
    })

    expect(wrapper.attributes('data-run-state')).toBe('recovering')
    expect(wrapper.get('.run-strip-title').text()).toContain('恢复连接')
  })

  it('pins an attention slot above the conversation timeline', () => {
    const wrapper = mount(ChatDock, {
      props: {
        projection: projection('blocked', 'act'), connectionState: 'connected',
        outputSignature: 'attention', messageCount: 1, sessionKey: 'session-1',
        timelineReady: true,
      },
      slots: { attention: '<button type="button">批准并继续</button>' },
    })

    expect(wrapper.get('.chat-attention').text()).toContain('批准并继续')
    expect(wrapper.get('.chat-attention').element.nextElementSibling?.classList).toContain('chat-dock-timeline')
  })
})
