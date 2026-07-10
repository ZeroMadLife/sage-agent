import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import CodingExecutionLog from './CodingExecutionLog.vue'

it('keeps completed execution details collapsed by default', async () => {
  const wrapper = mount(CodingExecutionLog, {
    props: {
      isThinking: false,
      activities: [
        { kind: 'tool', label: '读取 README.md', status: 'done' },
      ],
    },
  })

  expect(wrapper.text()).toContain('执行过程')
  expect(wrapper.find('.execution-log-list').exists()).toBe(false)

  await wrapper.find('.execution-log-header').trigger('click')
  expect(wrapper.find('.execution-log-list').exists()).toBe(true)
  expect(wrapper.text()).toContain('读取 README.md')
})

it('opens the execution log while a run is active', () => {
  const wrapper = mount(CodingExecutionLog, {
    props: {
      isThinking: true,
      activities: [
        { kind: 'model', label: '请求模型响应', status: 'running' },
      ],
    },
  })

  expect(wrapper.find('.execution-log-list').exists()).toBe(true)
  expect(wrapper.text()).toContain('进行中')
})
