import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it } from 'vitest'
import AssistantNavigation from './AssistantNavigation.vue'
import { closeCommandPalette, useCommandPalette } from '../product-shell'

afterEach(() => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 })
  localStorage.clear()
  closeCommandPalette(false)
})

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/assistant', component: { template: '<div />' } },
      { path: '/coding', component: { template: '<div />' } },
      { path: '/coding/session/:sessionId', component: { template: '<div />' } },
      { path: '/knowledge', component: { template: '<div />' } },
      { path: '/growth', component: { template: '<div />' } },
      { path: '/public', component: { template: '<div />' } },
      { path: '/settings/appearance', component: { template: '<div />' } },
    ],
  })
}

it('keeps only the approved primary destinations and treats coding sessions as main conversation', async () => {
  const router = createTestRouter()
  await router.push('/coding/session/session-a')
  const wrapper = mount(AssistantNavigation, {
    slots: { default: '<p>对话内容</p>' },
    global: { plugins: [router] },
  })

  const labels = wrapper.findAll('.desktop-primary-navigation a').map((link) => link.text())
  expect(labels).toEqual(['主对话', 'Knowledge'])
  expect(wrapper.text()).not.toContain('今天')
  expect(wrapper.text()).not.toContain('成长记录')
  expect(wrapper.get('a[href="/coding/session/session-a"]').classes()).toContain('active')
  expect(wrapper.get('.public-link').text()).toContain('公开门面')
  expect(wrapper.get('.settings-link').text()).toContain('设置')
  expect(wrapper.get('.assistant-shell').classes()).toContain('workspace-viewport')
})

it('only locks the application viewport for conversation workspaces', async () => {
  const router = createTestRouter()
  await router.push('/assistant')
  const wrapper = mount(AssistantNavigation, {
    slots: { default: '<p>主页内容</p>' },
    global: { plugins: [router] },
  })

  expect(wrapper.get('.assistant-shell').classes()).not.toContain('workspace-viewport')
  await router.push('/coding/session/session-a')
  expect(wrapper.get('.assistant-shell').classes()).toContain('workspace-viewport')
  wrapper.unmount()
})

it('uses a three-item bottom navigation and command trigger on mobile', async () => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
  const router = createTestRouter()
  await router.push('/assistant')
  const wrapper = mount(AssistantNavigation, {
    slots: { default: '<p>主页内容</p>' },
    global: { plugins: [router] },
    attachTo: document.body,
  })

  const navigation = wrapper.get('.mobile-bottom-navigation')
  expect(navigation.findAll('a').map((link) => link.text())).toEqual(['主对话', 'Knowledge', '设置'])
  expect(navigation.text()).not.toContain('公开门面')
  expect(navigation.text()).not.toContain('成长记录')

  await wrapper.get('button[aria-label="打开命令面板"]').trigger('click')
  expect(useCommandPalette().commandPaletteOpen.value).toBe(true)
  wrapper.unmount()
})

it('returns from knowledge to the most recent conversation', async () => {
  localStorage.setItem('sage.coding.recentSessionId', 'session-active')
  const router = createTestRouter()
  await router.push('/knowledge')
  const wrapper = mount(AssistantNavigation, {
    slots: { default: '<p>知识图谱</p>' },
    global: { plugins: [router] },
  })

  expect(wrapper.get('a[href="/coding/session/session-active"]').text()).toContain('主对话')
  wrapper.unmount()
})
