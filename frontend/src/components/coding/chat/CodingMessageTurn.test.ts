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
          activities: [{ kind: 'tool', label: '读取 README', status: 'done' }],
        },
        renderedContent: '<p>完成</p>',
      },
    })

    expect(wrapper.get('[aria-label="Sage"]').attributes('aria-label')).toBe('Sage')
    expect(wrapper.get('.execution-log').text()).toContain('已完成')
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

  it('keeps expandable execution metadata inside the assistant message body', async () => {
    const wrapper = mount(CodingMessageTurn, {
      props: {
        message: {
          role: 'assistant',
          content: '',
          activities: [{ kind: 'retry', label: '重试模型请求', status: 'running' }],
        },
        renderedContent: '',
      },
    })

    expect(wrapper.get('.message-body').find('.execution-log').exists()).toBe(true)
    await wrapper.get('.execution-log-header').trigger('click')
    expect(wrapper.get('.message-body').text()).toContain('重试模型请求')
  })

  it('does not render an empty assistant shell before any visible response exists', () => {
    const wrapper = mount(CodingMessageTurn, {
      props: {
        message: { role: 'assistant', content: '' },
        renderedContent: '',
        showProcess: false,
      },
    })

    expect(wrapper.find('.message-turn').exists()).toBe(false)
  })

  it('emits a run diff request from the assistant response header', async () => {
    const wrapper = mount(CodingMessageTurn, {
      props: {
        message: { role: 'assistant', content: '完成' },
        renderedContent: '<p>完成</p>',
        diffFileCount: 2,
      },
    })

    await wrapper.get('[aria-label="查看本轮 2 个变更文件"]').trigger('click')

    expect(wrapper.emitted('viewDiff')).toHaveLength(1)
  })
})
