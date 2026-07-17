import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it } from 'vitest'
import AssistantNavigation from './AssistantNavigation.vue'

afterEach(() => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 })
})

it('uses an accessible full-screen navigation drawer on mobile', async () => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/assistant', component: { template: '<div />' } },
      { path: '/coding', component: { template: '<div />' } },
      { path: '/knowledge', component: { template: '<div />' } },
      { path: '/evolution', component: { template: '<div />' } },
      { path: '/public', component: { template: '<div />' } },
      { path: '/settings/appearance', component: { template: '<div />' } },
    ],
  })
  await router.push('/assistant')
  const wrapper = mount(AssistantNavigation, {
    slots: { default: '<p>主页内容</p>' },
    global: { plugins: [router] },
    attachTo: document.body,
  })

  const trigger = wrapper.get('button[aria-label="打开主菜单"]')
  await trigger.trigger('click')
  expect(wrapper.get('[role="dialog"][aria-label="主菜单"]').attributes('aria-modal')).toBe('true')
  expect(wrapper.get('.assistant-main').attributes('inert')).toBeDefined()
  await wrapper.get('[role="dialog"]').trigger('keydown', { key: 'Escape' })
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  expect(document.activeElement).toBe(trigger.element)
  wrapper.unmount()
})
