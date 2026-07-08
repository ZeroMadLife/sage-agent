import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { useCodingStore } from '../stores/coding'
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

it('renders run detail as a readable worklog timeline', () => {
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

  expect(wrapper.text()).toContain('Run read_file')
  expect(wrapper.text()).toContain('path=README.md')
  expect(wrapper.text()).toContain('read_file succeeded')
  expect(wrapper.text()).toContain('Final answer')
  expect(wrapper.findAll('.run-timeline-entry')).toHaveLength(3)
})
