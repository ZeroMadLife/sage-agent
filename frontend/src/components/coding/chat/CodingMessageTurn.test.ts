import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import CodingMessageTurn from './CodingMessageTurn.vue'

describe('CodingMessageTurn', () => {
  it('renders the Sage identity with assistant work metadata', () => {
    const wrapper = mount(CodingMessageTurn, {
      props: {
        message: {
          role: 'assistant',
          content: '完成',
          activities: [{ kind: 'model', label: '请求模型响应', status: 'done' }],
        },
        renderedContent: '<p>完成</p>',
      },
      global: {
        stubs: {
          CodingExecutionLog: true,
          CodingToolActivity: true,
        },
      },
    })

    expect(wrapper.get('[aria-label="Sage"]').attributes('aria-label')).toBe('Sage')
    expect(wrapper.findComponent({ name: 'CodingExecutionLog' }).exists()).toBe(true)
    expect(wrapper.text()).toContain('Sage')
  })

  it('renders a distinct user identity without assistant metadata', () => {
    const wrapper = mount(CodingMessageTurn, {
      props: {
        message: { role: 'user', content: '检查项目' },
        renderedContent: '<p>检查项目</p>',
      },
    })

    expect(wrapper.get('[aria-label="用户"]').attributes('aria-label')).toBe('用户')
    expect(wrapper.text()).toContain('你')
  })

  it('exposes stable turn and run ids for timeline list reuse', () => {
    const wrapper = mount(CodingMessageTurn, {
      props: {
        message: {
          id: 'turn:run-1:assistant',
          run_id: 'run-1',
          role: 'assistant',
          content: '完成',
        },
        renderedContent: '<p>完成</p>',
      },
    })

    expect(wrapper.get('.message-turn').attributes('data-turn-id')).toBe('turn:run-1:assistant')
    expect(wrapper.get('.message-turn').attributes('data-run-id')).toBe('run-1')
  })

  it('hides tool activity when process visibility is disabled', () => {
    const wrapper = mount(CodingMessageTurn, {
      props: {
        message: {
          id: 'assistant-a',
          role: 'assistant',
          content: '完成',
          tools: [{ tool: 'read_file', args: {}, status: 'done', content: 'README' }],
        },
        renderedContent: '<p>完成</p>',
        showProcess: false,
      },
      global: { stubs: { CodingToolActivity: true, CodingExecutionLog: true } },
    })

    expect(wrapper.findComponent({ name: 'CodingToolActivity' }).exists()).toBe(false)
  })
})
