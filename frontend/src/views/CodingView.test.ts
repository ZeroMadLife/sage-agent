import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CodingView from './CodingView.vue'
import { useCodingStore } from '../stores/coding'
import type { CodingTimelineEvent } from '../types/api'

function event(sequence: number, kind: CodingTimelineEvent['kind'], payload: Record<string, unknown>): CodingTimelineEvent {
  return {
    event_id: `event-${sequence}`,
    session_id: 'session-a',
    run_id: 'run-a',
    sequence,
    kind,
    status: kind === 'terminal' ? 'completed' : 'running',
    timestamp: '2026-07-12T00:00:00Z',
    payload,
  }
}

function createTestRouter(initialPath = '/coding') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/coding', name: 'coding.home', component: CodingView },
      { path: '/coding/session/:sessionId', name: 'coding.session', component: CodingView },
      { path: '/settings/:section?', component: { template: '<div>settings</div>' } },
    ],
  })
  return router.push(initialPath).then(() => router)
}

async function flushScrollFrame() {
  await nextTick()
  await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()))
  await nextTick()
}

describe('CodingView chat route lifecycle', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 })
  })

  async function mountChat(initialPath = '/coding') {
    const router = await createTestRouter(initialPath)
    const wrapper = mount({ components: { RouterView }, template: '<RouterView />' }, {
      global: {
        plugins: [router],
        stubs: {
          CodingSidebar: true,
          CodingComposer: true,
          CodingGitBadge: true,
          CodingApprovalCard: true,
          CodingThinkingIndicator: true,
          CodingPlanApproval: true,
          CodingPlanPreview: true,
          CodingDiffDrawer: true,
        },
      },
    })
    return { router, root: wrapper, wrapper: () => wrapper.findComponent(CodingView) }
  }

  it('initializes a bare coding route once and replaces it with the created session URL', async () => {
    const store = useCodingStore()
    store.loadSessions = vi.fn().mockResolvedValue(undefined)
    store.initialize = vi.fn(async () => { store.sessionId = 'new-session' })
    const { router, root } = await mountChat()

    await vi.waitFor(() => expect(store.initialize).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/coding/session/new-session'))
    root.unmount()
  })

  it('restores a persisted recent session before creating a new one', async () => {
    localStorage.setItem('sage.coding.recentSessionId', 'saved-session')
    const store = useCodingStore()
    store.selectSession = vi.fn(async () => { store.sessionId = 'saved-session' })
    store.restoreCurrentSession = vi.fn()
    store.initialize = vi.fn()
    const { router, root } = await mountChat('/coding')

    await vi.waitFor(() => expect(store.selectSession).toHaveBeenCalledWith('saved-session'))
    expect(store.initialize).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/coding/session/saved-session'))
    expect(store.restoreCurrentSession).not.toHaveBeenCalled()
    root.unmount()
  })

  it('selects the most recently updated active session when no persisted session exists', async () => {
    const store = useCodingStore()
    store.loadSessions = vi.fn(async () => {
      store.codingSessions = [
        { session_id: 'older', title: '', workspace_root: '', created_at: '', updated_at: '2026-01-01T00:00:00Z', runtime_mode: 'default', message_count: 0 },
        { session_id: 'archived', title: '', workspace_root: '', created_at: '', updated_at: '2026-02-01T00:00:00Z', runtime_mode: 'default', message_count: 0, archived: true },
        { session_id: 'newest', title: '', workspace_root: '', created_at: '', updated_at: '2026-03-01T00:00:00Z', runtime_mode: 'default', message_count: 0 },
      ]
    })
    store.selectSession = vi.fn(async (sessionId: string) => { store.sessionId = sessionId })
    store.initialize = vi.fn()
    const { router, root } = await mountChat('/coding')

    await vi.waitFor(() => expect(store.loadSessions).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(store.selectSession).toHaveBeenCalledWith('newest'))
    expect(store.initialize).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/coding/session/newest'))
    root.unmount()
  })

  it('shows a session-list error instead of creating a session when history loading fails', async () => {
    const store = useCodingStore()
    store.loadSessions = vi.fn().mockRejectedValue(new Error('服务不可用'))
    store.initialize = vi.fn()
    const { root, wrapper } = await mountChat('/coding')

    await vi.waitFor(() => expect(wrapper().get('[role="alert"]').text()).toContain('无法打开会话'))
    expect(store.initialize).not.toHaveBeenCalled()
    root.unmount()
  })

  it('opens a direct session link without creating another session', async () => {
    const store = useCodingStore()
    store.selectSession = vi.fn(async () => { store.sessionId = 'saved-session' })
    store.initialize = vi.fn()
    const { root } = await mountChat('/coding/session/saved-session')

    await vi.waitFor(() => expect(store.selectSession).toHaveBeenCalledWith('saved-session'))
    expect(store.initialize).not.toHaveBeenCalled()
    root.unmount()
  })

  it('keeps an invalid session deep link visible as a recoverable error', async () => {
    const store = useCodingStore()
    store.selectSession = vi.fn().mockRejectedValue(new Error('会话不存在'))
    store.initialize = vi.fn()
    const { root, wrapper } = await mountChat('/coding/session/missing-session')

    await vi.waitFor(() => expect(wrapper().get('[role="alert"]').text()).toContain('无法打开会话'))
    expect(store.initialize).not.toHaveBeenCalled()
    root.unmount()
  })

  it('replays the current session after returning from settings without selecting a replacement', async () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.restoreCurrentSession = vi.fn()
    const { router, root } = await mountChat('/coding/session/session-a')

    await vi.waitFor(() => expect(store.restoreCurrentSession).toHaveBeenCalledTimes(1))
    await router.push('/settings/appearance')
    await router.push('/coding/session/session-a')
    await vi.waitFor(() => expect(store.restoreCurrentSession).toHaveBeenCalledTimes(2))
    root.unmount()
  })

  it('selects a changed session route and keeps the chat shell free of a persistent inspector', async () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.selectSession = vi.fn(async (sessionId: string) => { store.sessionId = sessionId })
    const { router, root, wrapper } = await mountChat('/coding/session/session-a')

    await router.push('/coding/session/session-b')
    await vi.waitFor(() => expect(store.selectSession).toHaveBeenCalledWith('session-b'))
    expect(wrapper().find('.pane-right').exists()).toBe(false)
    expect(wrapper().find('.chat-shell').exists()).toBe(true)
    root.unmount()
  })

  it('keeps session mutations in the chat view when the sidebar requests navigation', async () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.selectSession = vi.fn(async (sessionId: string) => { store.sessionId = sessionId })
    const { router, root, wrapper } = await mountChat('/coding/session/session-a')

    await wrapper().findComponent({ name: 'CodingSidebar' }).vm.$emit('navigate', 'session-b')

    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/coding/session/session-b'))
    await vi.waitFor(() => expect(store.selectSession).toHaveBeenCalledWith('session-b'))
    root.unmount()
  })

  it('archives the active session through the chat view and replaces its URL', async () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.setSessionArchived = vi.fn()
    store.startNewSession = vi.fn(async () => { store.sessionId = 'session-new' })
    const { router, root, wrapper } = await mountChat('/coding/session/session-a')

    await wrapper().findComponent({ name: 'CodingSidebar' }).vm.$emit('archiveCurrent', 'session-a')

    await vi.waitFor(() => expect(store.setSessionArchived).toHaveBeenCalledWith('session-a', true))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/coding/session/session-new'))
    expect(localStorage.getItem('sage.coding.recentSessionId')).toBe('session-new')
    root.unmount()
  })

  it('uses a compact modal drawer and central session title bar on tablet', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 })
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.codingSessions = [{ session_id: 'session-a', title: '修复路由', workspace_root: '', created_at: '', updated_at: '', runtime_mode: 'default', message_count: 0 }]
    const { root, wrapper } = await mountChat('/coding/session/session-a')

    expect(wrapper().find('.pane-left').exists()).toBe(false)
    expect(wrapper().get('.session-titlebar').text()).toContain('修复路由')
    expect(wrapper().find('.workbench-header .git-badge').exists()).toBe(false)
    await wrapper().get('button[aria-label="打开会话"]').trigger('click')
    expect(wrapper().get('.pane-left').attributes('role')).toBe('dialog')
    expect(wrapper().get('.session-backdrop').classes()).toContain('visible')
    expect(wrapper().get('.pane-center').attributes('aria-hidden')).toBe('true')
    expect(wrapper().get('.pane-center').attributes('inert')).toBeDefined()
    await wrapper().get('.pane-left').trigger('keydown', { key: 'Escape' })
    expect(wrapper().find('.pane-left').exists()).toBe(false)
    expect(wrapper().find('.pane-center').attributes('aria-hidden')).toBeUndefined()
    root.unmount()
  })

  it('opens Files from the session title bar without restoring a permanent inspector', async () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.loadFiles = vi.fn().mockResolvedValue(undefined)
    const { root, wrapper } = await mountChat('/coding/session/session-a')

    await wrapper().get('button[aria-label="打开文件"]').trigger('click')

    expect(store.loadFiles).toHaveBeenCalledWith('.')
    expect(wrapper().find('[role="dialog"][aria-label="工作区文件"]').exists()).toBe(true)
    expect(wrapper().find('.pane-right').exists()).toBe(false)
    root.unmount()
  })

  it('uses a full-screen drawer and isolates header and chat on mobile', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    const store = useCodingStore()
    store.sessionId = 'session-a'
    const { root, wrapper } = await mountChat('/coding/session/session-a')

    await wrapper().get('button[aria-label="打开会话"]').trigger('click')
    expect(wrapper().get('.pane-left').attributes('role')).toBe('dialog')
    expect(wrapper().get('.workbench-header').attributes('aria-hidden')).toBe('true')
    expect(wrapper().get('.workbench-header').attributes('inert')).toBeDefined()
    expect(wrapper().get('.pane-center').attributes('aria-hidden')).toBe('true')
    root.unmount()
  })

  it('closes the compact drawer after a successful session navigation', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 })
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.selectSession = vi.fn(async (sessionId: string) => { store.sessionId = sessionId })
    const { router, root, wrapper } = await mountChat('/coding/session/session-a')

    await wrapper().get('button[aria-label="打开会话"]').trigger('click')
    await wrapper().findComponent({ name: 'CodingSidebar' }).vm.$emit('navigate', 'session-b')

    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/coding/session/session-b'))
    await vi.waitFor(() => expect(wrapper().find('.pane-left').exists()).toBe(false))
    root.unmount()
  })

  it('keeps a compact drawer open and shows its error when navigation fails', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 })
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.selectSession = vi.fn().mockRejectedValue(new Error('会话不存在'))
    const { root, wrapper } = await mountChat('/coding/session/session-a')

    await wrapper().get('button[aria-label="打开会话"]').trigger('click')
    await wrapper().findComponent({ name: 'CodingSidebar' }).vm.$emit('navigate', 'missing')

    await vi.waitFor(() => expect(wrapper().get('.pane-left [role="alert"]').text()).toContain('无法切换会话'))
    expect(wrapper().find('.pane-left').exists()).toBe(true)
    root.unmount()
  })

  it('renders projected turns and preserves the session scroll anchor', async () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.mergeTimelinePage('session-a', [
      event(1, 'user', { type: 'user', content: '检查项目' }),
      event(2, 'assistant', { type: 'final', content: '检查完成' }),
    ], { next_cursor: 2, has_more: false, active_run: null })
    const { root, wrapper } = await mountChat('/coding/session/session-a')

    expect(wrapper().find('[data-timeline-turn-id="turn:run-a"]').exists()).toBe(true)
    const messageArea = wrapper().find<HTMLElement>('.message-area')
    messageArea.element.scrollTop = 24
    await messageArea.trigger('scroll')
    expect(store.scrollAnchor).toEqual({ eventId: 'turn:run-a', offset: 24 })
    root.unmount()
  })

  it('keeps the reader position and offers a return-to-bottom control for unseen messages', async () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    const { root, wrapper } = await mountChat('/coding/session/session-a')
    const messageArea = wrapper().find<HTMLElement>('.message-area')
    Object.defineProperties(messageArea.element, {
      scrollHeight: { configurable: true, value: 1_000 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, writable: true, value: 120 },
    })
    await messageArea.trigger('scroll')

    store.messages.push({ role: 'assistant', content: '新的后台结果' })
    await nextTick()

    const returnToBottom = wrapper().get('button[aria-label="回到底部，1 条新消息"]')
    expect(messageArea.element.scrollTop).toBe(120)
    await returnToBottom.trigger('click')
    await flushScrollFrame()
    expect(messageArea.element.scrollTop).toBe(1_000)
    expect(wrapper().find('button[aria-label*="回到底部"]').exists()).toBe(false)
    root.unmount()
  })

  it('follows new messages when the reader is already at the bottom', async () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    const { root, wrapper } = await mountChat('/coding/session/session-a')
    const messageArea = wrapper().find<HTMLElement>('.message-area')
    Object.defineProperties(messageArea.element, {
      scrollHeight: { configurable: true, value: 1_000 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, writable: true, value: 600 },
    })
    await messageArea.trigger('scroll')

    store.messages.push({ role: 'assistant', content: '自动跟随结果' })
    await flushScrollFrame()

    expect(messageArea.element.scrollTop).toBe(1_000)
    expect(wrapper().find('button[aria-label*="回到底部"]').exists()).toBe(false)
    root.unmount()
  })

  it('continues following streamed content changes while the reader is at the bottom', async () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.messages.push({ role: 'assistant', content: '正在输出' })
    const { root, wrapper } = await mountChat('/coding/session/session-a')
    const messageArea = wrapper().find<HTMLElement>('.message-area')
    Object.defineProperties(messageArea.element, {
      scrollHeight: { configurable: true, value: 1_000 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, writable: true, value: 600 },
    })
    await messageArea.trigger('scroll')

    store.messages[0].content = '正在输出更多内容'
    await flushScrollFrame()

    expect(messageArea.element.scrollTop).toBe(1_000)
    root.unmount()
  })
})
