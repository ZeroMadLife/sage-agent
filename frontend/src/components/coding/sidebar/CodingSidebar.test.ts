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

  expect(wrapper.text()).toContain('Sessions')
  expect(wrapper.text()).toContain('读 README')
  expect(wrapper.text()).toContain('2 messages')
  expect(wrapper.find('.session-item.active').exists()).toBe(true)
})

it('collapses Skills panel by default and expands it on toggle', async () => {
  const store = useCodingStore()
  store.skills = [
    { name: 'review', description: 'review code', source: 'builtin', argument_hint: '' },
  ]
  const wrapper = mount(CodingSidebar)

  // Skills collapsed by default -> skill content hidden.
  expect(wrapper.text()).not.toContain('/review')

  const skillsToggle = wrapper
    .findAll('.panel-toggle')
    .find((btn) => btn.text().includes('Skills'))
  await skillsToggle?.trigger('click')

  expect(wrapper.text()).toContain('/review')
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

  await wrapper.find('input[aria-label="Search sessions"]').setValue('bug')

  expect(wrapper.findAll('.session-item')).toHaveLength(1)
  expect(wrapper.text()).toContain('fix bug')
  expect(wrapper.text()).not.toContain('读 README')
})

it('limits sessions to 10 and offers a show-all button', async () => {
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

  expect(wrapper.findAll('.session-item')).toHaveLength(10)
  expect(wrapper.text()).toContain('显示全部 (12)')

  await wrapper.find('.show-all-sessions').trigger('click')

  expect(wrapper.findAll('.session-item')).toHaveLength(12)
  expect(wrapper.find('.show-all-sessions').exists()).toBe(false)
})

it('selects a persisted coding session from the sidebar', async () => {
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
  store.selectSession = vi.fn()
  const wrapper = mount(CodingSidebar)

  await wrapper.find('button.session-item').trigger('click')

  expect(store.selectSession).toHaveBeenCalledWith('s2')
})

it('starts a new coding session from the sidebar', async () => {
  const store = useCodingStore()
  store.startNewSession = vi.fn()
  const wrapper = mount(CodingSidebar)

  await wrapper.find('[data-testid="new-coding-session"]').trigger('click')

  expect(store.startNewSession).toHaveBeenCalled()
})

it('renders memory panel collapsed by default and expands to show the hint', async () => {
  const wrapper = mount(CodingSidebar)

  // Memory panel collapsed by default -> hint hidden.
  expect(wrapper.text()).not.toContain('记住项目约定')

  const memoryToggle = wrapper
    .findAll('.panel-toggle')
    .find((btn) => btn.text().includes('记忆'))
  await memoryToggle?.trigger('click')

  expect(wrapper.text()).toContain('/remember')
  expect(wrapper.text()).toContain('记住项目约定')
  expect(wrapper.text()).toContain('/dream')
  expect(wrapper.text()).toContain('整理记忆')
})

it('renders run detail as a readable worklog timeline', async () => {
  const store = useCodingStore()
  store.runs = [
    {
      run_id: 'run_abc123',
      status: 'completed',
      event_count: 5,
      tool_count: 1,
      error_count: 0,
      last_event_type: 'final',
      started_at: '2026-07-08T10:00:00',
      updated_at: '2026-07-08T10:00:01',
    },
  ]
  store.selectedRun = {
    run_id: 'run_abc123',
    events: [{ type: 'tool_call' }],
    timeline: [
      {
        kind: 'tool',
        title: 'Run read_file',
        detail: 'path=README.md',
        status: 'running',
        tool: 'read_file',
        timestamp: '2026-07-08T10:00:00',
      },
      {
        kind: 'result',
        title: 'read_file succeeded',
        detail: '# Sage',
        status: 'done',
        tool: 'read_file',
        timestamp: '2026-07-08T10:00:01',
      },
      {
        kind: 'final',
        title: 'Final answer',
        detail: 'README 总结完成。',
        status: 'done',
        tool: '',
        timestamp: '2026-07-08T10:00:02',
      },
    ],
  }

  const wrapper = mount(CodingSidebar)

  // Runs panel is collapsed by default; expand it to reveal the timeline.
  const runsToggle = wrapper
    .findAll('.panel-toggle')
    .find((btn) => btn.text().includes('Runs'))
  await runsToggle?.trigger('click')

  expect(wrapper.text()).toContain('Run read_file')
  expect(wrapper.text()).toContain('path=README.md')
  expect(wrapper.text()).toContain('read_file succeeded')
  expect(wrapper.text()).toContain('Final answer')
  expect(wrapper.findAll('.run-timeline-entry')).toHaveLength(3)
})
