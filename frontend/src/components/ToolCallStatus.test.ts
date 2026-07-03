import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import ToolCallTrace from './ToolCallTrace.vue'

it('renders a collapsible human-readable tool trace without raw JSON', () => {
  const wrapper = mount(ToolCallTrace, {
    props: {
      toolCalls: [
        {
          tool: 'get_forecast',
          status: 'running',
          message: '正在查询杭州未来 7 天天气',
          args: { city: '杭州', days: 7 },
        },
      ],
    },
  })

  expect(wrapper.find('details').exists()).toBe(true)
  expect(wrapper.text()).toContain('思考中')
  expect(wrapper.text()).toContain('正在查询杭州未来 7 天天气')
  expect(wrapper.text()).toContain('工具：天气预报')
  expect(wrapper.text()).toContain('城市：杭州')
  expect(wrapper.text()).not.toContain('{"city"')
})
