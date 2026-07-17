import { mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import CodingThinkingIndicator from './CodingThinkingIndicator.vue'

afterEach(() => vi.useRealTimers())

const stubs = {
  SageThinkingCharacter: {
    props: ['state', 'phase'],
    template: '<span class="sage-character" :data-state="state" :data-phase="phase"></span>',
  },
}

it('renders the thinking phase text', () => {
  const wrapper = mount(CodingThinkingIndicator, {
    props: { phase: '正在请求模型...' },
    global: { stubs },
  })

  expect(wrapper.text()).toContain('正在请求模型...')
  expect(wrapper.find('.thinking-phase').text()).toBe('正在请求模型...')
  expect(wrapper.text()).toContain('正在思考')
  expect(wrapper.find('.sage-character').attributes('data-state')).toBe('thinking')
  expect(wrapper.find('.thinking-time').text()).toBe('0s')
  expect(wrapper.attributes('role')).toBe('status')
})

it('renders an arbitrary phase', () => {
  const wrapper = mount(CodingThinkingIndicator, {
    props: { phase: '思考中', state: 'tool' },
    global: { stubs },
  })

  expect(wrapper.text()).toContain('思考中')
  expect(wrapper.find('.sage-character').attributes('data-state')).toBe('tool')
})

it('shows public elapsed time without rendering internal reasoning text', async () => {
  vi.useFakeTimers()
  const wrapper = mount(CodingThinkingIndicator, {
    props: { phase: '正在执行工具' },
    global: { stubs },
  })

  await vi.advanceTimersByTimeAsync(2_000)

  expect(wrapper.find('.thinking-time').text()).toBe('2s')
  expect(wrapper.text()).not.toContain('chain-of-thought')
  wrapper.unmount()
})

it('renders the completed character state without an elapsed timer', () => {
  const wrapper = mount(CodingThinkingIndicator, {
    props: { phase: '整理本轮结果', state: 'done', title: '想到了', showElapsed: false },
    global: { stubs },
  })

  expect(wrapper.text()).toContain('想到了')
  expect(wrapper.find('.sage-character').attributes('data-state')).toBe('done')
  expect(wrapper.find('.thinking-time').exists()).toBe(false)
})
