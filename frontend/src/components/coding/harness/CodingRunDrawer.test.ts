import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import CodingRunDrawer from './CodingRunDrawer.vue'

it('renders the complete child timeline and closes on command', async () => {
  const wrapper = mount(CodingRunDrawer, {
    props: {
      visible: true,
      runId: 'child-1',
      run: {
        run_id: 'child-1',
        events: [],
        timeline: [{
          kind: 'tool',
          title: 'Run read_file',
          detail: 'README.md',
          status: 'done',
          tool: 'read_file',
          timestamp: '2026-07-18T08:30:00Z',
        }],
      },
    },
  })

  expect(wrapper.get('[role="dialog"]').text()).toContain('child-1')
  expect(wrapper.get('.run-timeline').text()).toContain('Run read_file')
  expect(wrapper.get('.run-timeline').text()).toContain('README.md')
  await wrapper.get('button[aria-label="关闭子代理运行详情"]').trigger('click')
  expect(wrapper.emitted('close')).toHaveLength(1)
})

it('shows a loading state without exposing stale run content', () => {
  const wrapper = mount(CodingRunDrawer, {
    props: { visible: true, runId: 'child-2', run: null },
  })

  expect(wrapper.get('.run-loading').text()).toContain('正在读取运行记录')
  expect(wrapper.find('.run-timeline').exists()).toBe(false)
})

it('shows a bounded load error instead of an endless spinner', () => {
  const wrapper = mount(CodingRunDrawer, {
    props: {
      visible: true,
      runId: 'child-missing',
      run: null,
      error: '无法读取该子运行',
    },
  })

  expect(wrapper.get('.run-error').text()).toContain('无法读取该子运行')
  expect(wrapper.find('.run-loading').exists()).toBe(false)
})
