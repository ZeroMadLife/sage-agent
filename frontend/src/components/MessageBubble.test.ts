import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import MessageBubble from './MessageBubble.vue'

it('renders a thinking state for empty assistant messages', () => {
  const wrapper = mount(MessageBubble, {
    props: {
      role: 'assistant',
      content: '',
      isThinking: true,
      statusMessage: '正在思考中...',
    },
  })

  expect(wrapper.text()).toContain('正在思考中')
})
