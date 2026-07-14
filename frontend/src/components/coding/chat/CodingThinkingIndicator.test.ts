import { mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import CodingThinkingIndicator from './CodingThinkingIndicator.vue'

afterEach(() => vi.useRealTimers())

it('renders the thinking phase text', () => {
  const wrapper = mount(CodingThinkingIndicator, {
    props: { phase: '正在请求模型...' },
  })

  expect(wrapper.text()).toContain('正在请求模型...')
  expect(wrapper.find('.thinking-phase').text()).toBe('正在请求模型...')
  expect(wrapper.text()).toContain('正在思考')
  expect(wrapper.find('.sage-avatar').exists()).toBe(true)
  expect(wrapper.find('.thinking-time').text()).toBe('0s')
  expect(wrapper.attributes('role')).toBe('status')
})

it('renders an arbitrary phase', () => {
  const wrapper = mount(CodingThinkingIndicator, {
    props: { phase: '思考中' },
  })

  expect(wrapper.text()).toContain('思考中')
})

it('shows public elapsed time without rendering internal reasoning text', async () => {
  vi.useFakeTimers()
  const wrapper = mount(CodingThinkingIndicator, { props: { phase: '正在执行工具' } })

  await vi.advanceTimersByTimeAsync(2_000)

  expect(wrapper.find('.thinking-time').text()).toBe('2s')
  expect(wrapper.text()).not.toContain('chain-of-thought')
  wrapper.unmount()
})
