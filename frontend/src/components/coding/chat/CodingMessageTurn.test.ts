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
})
