import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import AssistantHomeView from './AssistantHomeView.vue'
import { useAssistantHomeStore } from '../stores/assistantHome'
import { useCodingStore } from '../stores/coding'
import type { AssistantHomeSummary } from '../types/api'

function summary(): AssistantHomeSummary {
  return {
    identity: { mode: 'local', user_id: null, display_name: '本地工作区' },
    knowledge: { status: 'not_configured', source_count: 0, wiki_page_count: 0, last_synced_at: null },
    sessions: {
      status: 'ready', total: 1, error: null,
      items: [{
        session_id: 'recent', title: '复盘 Sage', workspace_name: 'tour-agent',
        updated_at: '2026-07-15T00:00:00Z', message_count: 4,
        target: '/coding/session/recent',
      }],
    },
    projects: { status: 'empty', items: [], total: 0, error: null },
    proposals: { status: 'ready', memory_pending: 1, wiki_pending: 0, note_pending: 0, error: null },
    suggested_actions: [{
      id: 'review-memory', kind: 'review', label: '查看待确认沉淀',
      description: '有 1 条记忆提案等待处理。', target: '/growth',
    }],
  }
}

async function mountHome() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/assistant', component: AssistantHomeView },
      { path: '/coding', component: { template: '<div />' } },
      { path: '/coding/session/:sessionId', component: { template: '<div />' } },
      { path: '/knowledge', component: { template: '<div />' } },
      { path: '/growth', component: { template: '<div />' } },
      { path: '/public', component: { template: '<div />' } },
      { path: '/settings/appearance', component: { template: '<div />' } },
    ],
  })
  await router.push('/assistant')
  const wrapper = mount(AssistantHomeView, {
    global: { plugins: [router] },
    attachTo: document.body,
  })
  return { router, wrapper }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 })
})

it('loads a real summary without creating a coding session on mount', async () => {
  const home = useAssistantHomeStore()
  const coding = useCodingStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  coding.startSessionWithPrompt = vi.fn()

  const { wrapper } = await mountHome()

  expect(home.load).toHaveBeenCalledTimes(1)
  expect(coding.startSessionWithPrompt).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('复盘 Sage')
  expect(wrapper.text()).toContain('待确认沉淀')
  wrapper.unmount()
})

it('creates one session with the prompt and routes to the existing chat', async () => {
  const home = useAssistantHomeStore()
  const coding = useCodingStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  coding.startSessionWithPrompt = vi.fn().mockResolvedValue('new-session')
  const { router, wrapper } = await mountHome()

  await wrapper.get('textarea[aria-label="给 Sage 的消息"]').setValue('学习 timeline replay')
  await wrapper.get('button[aria-label="发送消息"]').trigger('click')

  expect(coding.startSessionWithPrompt).toHaveBeenCalledTimes(1)
  expect(coding.startSessionWithPrompt).toHaveBeenCalledWith('学习 timeline replay')
  await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/coding/session/new-session'))
  expect(localStorage.getItem('sage.coding.recentSessionId')).toBe('new-session')
  wrapper.unmount()
})

it('preserves the prompt and shows a visible error when session creation fails', async () => {
  const home = useAssistantHomeStore()
  const coding = useCodingStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  coding.startSessionWithPrompt = vi.fn().mockRejectedValue(new Error('服务不可用'))
  const { wrapper } = await mountHome()
  const textarea = wrapper.get('textarea[aria-label="给 Sage 的消息"]')

  await textarea.setValue('不要丢失这段草稿')
  await wrapper.get('button[aria-label="发送消息"]').trigger('click')

  expect(wrapper.get('[role="alert"]').text()).toContain('服务不可用')
  expect((textarea.element as HTMLTextAreaElement).value).toBe('不要丢失这段草稿')
  wrapper.unmount()
})

it('focuses the composer when a suggested action targets compose', async () => {
  const home = useAssistantHomeStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  const { router, wrapper } = await mountHome()

  await router.push('/assistant?action=compose')
  const textarea = wrapper.get('textarea[aria-label="给 Sage 的消息"]')
  await vi.waitFor(() => expect(document.activeElement).toBe(textarea.element))
  wrapper.unmount()
})
