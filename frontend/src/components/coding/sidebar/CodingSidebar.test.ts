import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { useCodingStore } from '../../../stores/coding'
import CodingSidebar from './CodingSidebar.vue'

beforeEach(() => {
  setActivePinia(createPinia())
})

it('renders coding session history', () => {
  const store = useCodingStore()
  store.codingSessions = [
    {
      session_id: 's1',
      title: '读 README',
      workspace_root: '/tmp/repo',
      created_at: '2026-07-08T10:00:00',
      updated_at: '2026-07-08T10:00:01',
      runtime_mode: 'default',
      message_count: 2,
    },
  ]
  store.sessionId = 's1'

  const wrapper = mount(CodingSidebar)

  expect(wrapper.text()).toContain('Sage 工作区')
  expect(wrapper.text()).toContain('读 README')
  expect(wrapper.text()).toContain('2 条消息')
  expect(wrapper.find('.session-row.active').exists()).toBe(true)
})

it('filters sessions by title via the search box', async () => {
  const store = useCodingStore()
  store.codingSessions = [
    {
      session_id: 's1',
      title: '读 README',
      workspace_root: '/tmp',
      created_at: '',
      updated_at: '',
      runtime_mode: 'default',
      message_count: 1,
    },
    {
      session_id: 's2',
      title: 'fix bug',
      workspace_root: '/tmp',
      created_at: '',
      updated_at: '',
      runtime_mode: 'default',
      message_count: 1,
    },
  ]
  const wrapper = mount(CodingSidebar)

  expect(wrapper.findAll('.session-item')).toHaveLength(2)

  await wrapper.find('input[aria-label="搜索会话"]').setValue('bug')

  expect(wrapper.findAll('.session-item')).toHaveLength(1)
  expect(wrapper.text()).toContain('fix bug')
  expect(wrapper.text()).not.toContain('读 README')
})

it('keeps a compact session list without truncating a normal history', () => {
  const store = useCodingStore()
  store.codingSessions = Array.from({ length: 12 }, (_, i) => ({
    session_id: `s${i}`,
    title: `session ${i}`,
    workspace_root: '/tmp',
    created_at: '',
    updated_at: '',
    runtime_mode: 'default',
    message_count: 1,
  }))
  const wrapper = mount(CodingSidebar)

  expect(wrapper.findAll('.session-item')).toHaveLength(12)
  // Compact rail only paginates beyond 18 sessions.
  expect(wrapper.find('.show-all-sessions').exists()).toBe(false)

  expect(wrapper.findAll('.session-item')).toHaveLength(12)
})

it('requests a persisted coding session from the sidebar without mutating the store', async () => {
  const store = useCodingStore()
  store.codingSessions = [
    {
      session_id: 's2',
      title: '继续任务',
      workspace_root: '/tmp/repo',
      created_at: '2026-07-08T10:00:00',
      updated_at: '2026-07-08T10:00:01',
      runtime_mode: 'default',
      message_count: 4,
    },
  ]
  const wrapper = mount(CodingSidebar)

  await wrapper.find('button.session-item').trigger('click')

  expect(wrapper.emitted('navigate')?.[0]).toEqual(['s2'])
  expect(store.sessionId).not.toBe('s2')
})

it('requests a new coding session from the sidebar', async () => {
  const wrapper = mount(CodingSidebar)

  await wrapper.find('[data-testid="new-coding-session"]').trigger('click')

  expect(wrapper.emitted('newSession')).toHaveLength(1)
})

it('keeps skills, memory and runs out of the focused session rail', () => {
  const wrapper = mount(CodingSidebar)
  expect(wrapper.text()).not.toContain('Skills')
  expect(wrapper.text()).not.toContain('记忆提案')
  expect(wrapper.text()).not.toContain('运行记录')
})

it('exposes the settings entry from the session rail', async () => {
  const wrapper = mount(CodingSidebar)

  await wrapper.get('button[aria-label="打开设置"]').trigger('click')

  expect(wrapper.emitted('settings')).toHaveLength(1)
})

it('pins, renames, archives, and restores a session through the store actions', async () => {
  const store = useCodingStore()
  store.codingSessions = [{
    session_id: 's1', title: '旧名称', workspace_root: '/tmp', created_at: '', updated_at: '',
    runtime_mode: 'default', message_count: 1, pinned: false, archived: false,
  }]
  store.setSessionPinned = vi.fn().mockResolvedValue(undefined)
  store.renameSession = vi.fn().mockResolvedValue(undefined)
  store.setSessionArchived = vi.fn().mockResolvedValue(undefined)
  store.loadSessions = vi.fn().mockResolvedValue(undefined)
  const wrapper = mount(CodingSidebar)

  await wrapper.get('button[aria-label="置顶"]').trigger('click')
  expect(store.setSessionPinned).toHaveBeenCalledWith('s1', true)

  await wrapper.get('button[aria-label="重命名"]').trigger('click')
  await wrapper.get('input[aria-label="会话名称"]').setValue('新名称')
  await wrapper.get('input[aria-label="会话名称"]').trigger('keydown.enter')
  expect(store.renameSession).toHaveBeenCalledWith('s1', '新名称')

  await wrapper.get('button[aria-label="归档"]').trigger('click')
  expect(store.setSessionArchived).toHaveBeenCalledWith('s1', true)
})

it('keeps focusable rename controls outside the session selection button', async () => {
  const store = useCodingStore()
  store.codingSessions = [{
    session_id: 's1', title: '旧名称', workspace_root: '/tmp', created_at: '', updated_at: '',
    runtime_mode: 'default', message_count: 1, pinned: false, archived: false,
  }]
  const wrapper = mount(CodingSidebar)

  await wrapper.get('button[aria-label="重命名"]').trigger('click')

  expect(wrapper.findAll('button.session-item input')).toHaveLength(0)
  expect(wrapper.get('input[aria-label="会话名称"]').element).toBeInstanceOf(HTMLInputElement)
})

it('emits navigation and creation intentions without calling the session store', async () => {
  const store = useCodingStore()
  store.codingSessions = [{
    session_id: 's1', title: '失败会话', workspace_root: '/tmp', created_at: '', updated_at: '',
    runtime_mode: 'default', message_count: 1,
  }]
  const wrapper = mount(CodingSidebar)

  await expect(wrapper.get('button.session-item').trigger('click')).resolves.toBeUndefined()
  expect(wrapper.emitted('navigate')?.[0]).toEqual(['s1'])

  await expect(wrapper.get('[data-testid="new-coding-session"]').trigger('click')).resolves.toBeUndefined()
  expect(wrapper.emitted('newSession')).toHaveLength(1)
})

it('keeps rename editing open and reports a rejected metadata update', async () => {
  const store = useCodingStore()
  store.codingSessions = [{
    session_id: 's1', title: '旧名称', workspace_root: '/tmp', created_at: '', updated_at: '',
    runtime_mode: 'default', message_count: 1, pinned: false, archived: false,
  }]
  store.renameSession = vi.fn().mockRejectedValue(new Error('保存失败'))
  const wrapper = mount(CodingSidebar)

  await wrapper.get('button[aria-label="重命名"]').trigger('click')
  await wrapper.get('input[aria-label="会话名称"]').setValue('新名称')
  await expect(wrapper.get('input[aria-label="会话名称"]').trigger('keydown.enter')).resolves.toBeUndefined()

  await vi.waitFor(() => expect(wrapper.get('[role="alert"]').text()).toContain('无法重命名会话'))
  expect(wrapper.find('input[aria-label="会话名称"]').exists()).toBe(true)
})

it('reports rejected pin updates and delegates current-session archival to the view', async () => {
  const store = useCodingStore()
  store.sessionId = 's1'
  store.codingSessions = [{
    session_id: 's1', title: '当前会话', workspace_root: '/tmp', created_at: '', updated_at: '',
    runtime_mode: 'default', message_count: 1, pinned: false, archived: false,
  }]
  store.setSessionPinned = vi.fn().mockRejectedValue(new Error('置顶失败'))
  store.setSessionArchived = vi.fn().mockRejectedValue(new Error('归档失败'))
  store.loadSessions = vi.fn().mockResolvedValue(undefined)
  store.startNewSession = vi.fn().mockResolvedValue(undefined)
  const wrapper = mount(CodingSidebar)

  await expect(wrapper.get('button[aria-label="置顶"]').trigger('click')).resolves.toBeUndefined()
  await vi.waitFor(() => expect(wrapper.get('[role="alert"]').text()).toContain('无法置顶会话'))
  expect(store.loadSessions).not.toHaveBeenCalled()

  await expect(wrapper.get('button[aria-label="归档"]').trigger('click')).resolves.toBeUndefined()
  expect(wrapper.emitted('archiveCurrent')?.[0]).toEqual(['s1'])
  expect(store.loadSessions).not.toHaveBeenCalled()
  expect(wrapper.find('button.session-item').exists()).toBe(true)
})

it('reports a rejected restore action and keeps the archived session visible', async () => {
  const store = useCodingStore()
  store.codingSessions = [{
    session_id: 's1', title: '归档会话', workspace_root: '/tmp', created_at: '', updated_at: '',
    runtime_mode: 'default', message_count: 1, pinned: false, archived: true,
  }]
  store.setSessionArchived = vi.fn().mockRejectedValue(new Error('恢复失败'))
  store.loadSessions = vi.fn().mockResolvedValue(undefined)
  const wrapper = mount(CodingSidebar)

  await wrapper.get('button.archive-toggle').trigger('click')
  await expect(wrapper.get('button[aria-label="恢复会话"]').trigger('click')).resolves.toBeUndefined()

  await vi.waitFor(() => expect(wrapper.get('[role="alert"]').text()).toContain('无法恢复会话'))
  expect(store.loadSessions).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('归档会话')
})
