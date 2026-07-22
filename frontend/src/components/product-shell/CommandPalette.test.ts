import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it } from 'vitest'
import { nextTick } from 'vue'
import CommandPalette from './CommandPalette.vue'
import { closeCommandPalette, openCommandPalette } from './useCommandPalette'

afterEach(() => {
  closeCommandPalette(false)
  document.body.style.overflow = ''
})

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/assistant', component: { template: '<div />' } },
      { path: '/knowledge', component: { template: '<div />' } },
      { path: '/public', component: { template: '<div />' } },
      { path: '/settings/:section?', component: { template: '<div />' } },
    ],
  })
}

it('opens with the platform shortcut, filters commands, and navigates with Enter', async () => {
  const router = createTestRouter()
  await router.push('/assistant')
  const wrapper = mount(CommandPalette, {
    global: { plugins: [router] },
    attachTo: document.body,
  })

  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))
  await nextTick()
  expect(wrapper.find('[role="dialog"][aria-label="命令面板"]').exists()).toBe(true)

  const input = wrapper.get('input[aria-label="搜索命令"]')
  expect(document.activeElement).toBe(input.element)
  await input.setValue('记忆')
  expect(wrapper.findAll('[role="option"]')).toHaveLength(1)
  expect(wrapper.get('[role="option"]').text()).toContain('记忆')

  await input.trigger('keydown', { key: 'Enter' })
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe('/settings/memory')
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  wrapper.unmount()
})

it('locks background scroll and closes on Escape after focus leaves search', async () => {
  const router = createTestRouter()
  await router.push('/assistant')
  const wrapper = mount(CommandPalette, {
    global: { plugins: [router] },
    attachTo: document.body,
  })
  const trigger = document.createElement('button')
  document.body.appendChild(trigger)
  trigger.focus()

  openCommandPalette(trigger)
  await nextTick()
  expect(document.body.style.overflow).toBe('hidden')
  const closeButton = wrapper.get('button[aria-label="关闭命令面板"]')
  const closeButtonElement = closeButton.element as HTMLElement
  closeButtonElement.focus()
  await closeButton.trigger('keydown', { key: 'Escape' })
  await nextTick()

  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  expect(document.body.style.overflow).toBe('')
  expect(document.activeElement).toBe(trigger)
  trigger.remove()
  wrapper.unmount()
})
