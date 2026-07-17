import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import ChatTimeline from './ChatTimeline.vue'

function setScrollGeometry(
  element: HTMLElement,
  values: { scrollHeight: number; clientHeight: number; scrollTop: number },
) {
  Object.defineProperties(element, {
    scrollHeight: { configurable: true, value: values.scrollHeight },
    clientHeight: { configurable: true, value: values.clientHeight },
    scrollTop: { configurable: true, writable: true, value: values.scrollTop },
  })
}

async function flushScrollFrame() {
  await nextTick()
  await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()))
  await nextTick()
}

function mountTimeline(overrides: Record<string, unknown> = {}) {
  return mount(ChatTimeline, {
    props: {
      outputSignature: 'initial',
      messageCount: 0,
      sessionKey: 'session-a',
      timelineReady: true,
      ...overrides,
    },
    slots: {
      default: '<div data-timeline-turn-id="turn-a">turn</div>',
    },
  })
}

describe('ChatTimeline', () => {
  it('reports the visible turn as a durable scroll anchor', async () => {
    const wrapper = mountTimeline()
    const scroller = wrapper.get<HTMLElement>('.chat-timeline')
    setScrollGeometry(scroller.element, { scrollHeight: 1_000, clientHeight: 400, scrollTop: 24 })

    await scroller.trigger('scroll')

    expect(wrapper.emitted('anchorChange')).toEqual([['turn-a', 24]])
  })

  it('preserves reader position and counts unseen messages away from the bottom', async () => {
    const wrapper = mountTimeline()
    const scroller = wrapper.get<HTMLElement>('.chat-timeline')
    setScrollGeometry(scroller.element, { scrollHeight: 1_000, clientHeight: 400, scrollTop: 120 })
    await scroller.trigger('scroll')

    await wrapper.setProps({ messageCount: 1, outputSignature: 'new-message' })
    await nextTick()

    const returnButton = wrapper.get('button[aria-label="回到底部，1 条新消息"]')
    expect(scroller.element.scrollTop).toBe(120)
    await returnButton.trigger('click')
    await flushScrollFrame()

    expect(scroller.element.scrollTop).toBe(1_000)
    expect(wrapper.find('button[aria-label*="回到底部"]').exists()).toBe(false)
  })

  it('continues following streamed output while the reader is at the bottom', async () => {
    const wrapper = mountTimeline({ messageCount: 1 })
    const scroller = wrapper.get<HTMLElement>('.chat-timeline')
    setScrollGeometry(scroller.element, { scrollHeight: 1_000, clientHeight: 400, scrollTop: 600 })
    await scroller.trigger('scroll')

    await wrapper.setProps({ outputSignature: 'streamed-content' })
    await flushScrollFrame()

    expect(scroller.element.scrollTop).toBe(1_000)
  })

  it('restores a later durable anchor after an earlier reader scroll', async () => {
    const wrapper = mountTimeline({
      timelineReady: false,
      scrollAnchor: { eventId: 'turn-a', offset: 24 },
    })
    const scroller = wrapper.get<HTMLElement>('.chat-timeline')
    setScrollGeometry(scroller.element, { scrollHeight: 1_000, clientHeight: 400, scrollTop: 120 })
    await scroller.trigger('scroll')

    await wrapper.setProps({
      timelineReady: true,
      scrollAnchor: { eventId: 'turn-a', offset: 50 },
    })
    await nextTick()

    expect(scroller.element.scrollTop).toBe(50)
  })

  it('loads older records through the injected surface callback', async () => {
    const loadOlder = vi.fn().mockResolvedValue(undefined)
    const wrapper = mountTimeline({ hasMore: true, loadOlder })

    await wrapper.get('button').trigger('click')

    expect(loadOlder).toHaveBeenCalledTimes(1)
  })
})
