import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ToolCallTrace from './ToolCallTrace.vue'

describe('ToolCallTrace', () => {
  it('shows partial failure when a turn has an error tool but still produced an answer', () => {
    const wrapper = mount(ToolCallTrace, {
      props: {
        isThinking: false,
        toolCalls: [
          {
            tool: 'search_attractions',
            args: { city: '莆田' },
            status: 'done',
            message: '景点搜索',
          },
          {
            tool: 'get_forecast',
            args: { city: '莆田', days: 7 },
            status: 'error',
            message: '莆田天气预报失败',
          },
        ],
      },
    })

    expect(wrapper.text()).toContain('部分工具失败')
    expect(wrapper.text()).not.toContain('思考中断')
  })
})
