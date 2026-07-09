import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import CodingThinkingIndicator from './CodingThinkingIndicator.vue'

it('renders the thinking phase text', () => {
  const wrapper = mount(CodingThinkingIndicator, {
    props: { phase: '正在请求模型...' },
  })

  expect(wrapper.text()).toContain('正在请求模型...')
  expect(wrapper.find('.thinking-phase').text()).toBe('正在请求模型...')
  expect(wrapper.findAll('.thinking-dots .dot')).toHaveLength(3)
  expect(wrapper.attributes('role')).toBe('status')
})

it('renders an arbitrary phase', () => {
  const wrapper = mount(CodingThinkingIndicator, {
    props: { phase: '思考中' },
  })

  expect(wrapper.text()).toContain('思考中')
})
