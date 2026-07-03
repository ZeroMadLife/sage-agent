import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import AgentProgressPanel from './AgentProgressPanel.vue'

it('renders agent progress events', () => {
  const wrapper = mount(AgentProgressPanel, {
    props: {
      events: [{ type: 'progress', agent: 'planning', message: '正在生成行程' }],
    },
  })

  expect(wrapper.text()).toContain('planning')
  expect(wrapper.text()).toContain('正在生成行程')
})
