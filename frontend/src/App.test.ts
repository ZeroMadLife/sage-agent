import { createPinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import App from './App.vue'
import { AssistantNavigation } from './components/assistant'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }))
})

it('keeps one shared assistant shell while personal routes change', async () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/assistant', meta: { assistantShell: true }, component: { template: '<p>今天</p>' } },
      { path: '/coding', meta: { assistantShell: true }, component: { template: '<p>对话</p>' } },
      { path: '/knowledge', meta: { assistantShell: true }, component: { template: '<p>知识</p>' } },
      { path: '/evolution', meta: { assistantShell: true }, component: { template: '<p>成长</p>' } },
      { path: '/public', meta: { assistantShell: true }, component: { template: '<p>公开</p>' } },
      { path: '/settings/appearance', component: { template: '<p>设置</p>' } },
    ],
  })
  await router.push('/assistant')
  const wrapper = mount(App, {
    global: { plugins: [createPinia(), router] },
    attachTo: document.body,
  })
  await nextTick()
  const shellElement = wrapper.findComponent(AssistantNavigation).element

  await router.push('/coding')
  await nextTick()
  expect(wrapper.findComponent(AssistantNavigation).element).toBe(shellElement)
  expect(wrapper.text()).toContain('对话')

  await router.push('/settings/appearance')
  await nextTick()
  expect(wrapper.findComponent(AssistantNavigation).exists()).toBe(false)
  expect(wrapper.text()).toContain('设置')
  wrapper.unmount()
})
