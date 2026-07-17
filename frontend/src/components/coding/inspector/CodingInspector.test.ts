import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import CodingInspector from './CodingInspector.vue'
import { useCodingStore } from '../../../stores/coding'

describe('CodingInspector', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('provides files, changes, runs and memory views plus a disabled terminal', async () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.runs = [{
      run_id: 'run-a', status: 'completed', event_count: 3, tool_count: 1,
      error_count: 0, last_event_type: 'run_finished', started_at: '', updated_at: '',
    }]

    const wrapper = mount(CodingInspector, {
      global: { stubs: { CodingFileTree: true } },
    })
    expect(wrapper.get('[role="tablist"]').text()).toContain('文件')
    expect(wrapper.get('[role="tablist"]').text()).toContain('变更')
    expect(wrapper.get('[role="tablist"]').text()).toContain('运行')
    expect(wrapper.get('[role="tablist"]').text()).toContain('记忆')

    await wrapper.get('[data-tab="runs"]').trigger('click')
    expect(wrapper.text()).toContain('run-a')
    expect(wrapper.text()).toContain('终端将在 V7 沙箱就绪后开放')
  })

  it('links tabs to the active panel and supports keyboard tab navigation', async () => {
    const wrapper = mount(CodingInspector, {
      global: { stubs: { CodingFileTree: true } },
      attachTo: document.body,
    })
    const filesTab = wrapper.get('[data-tab="files"]')
    const changesTab = wrapper.get('[data-tab="changes"]')
    const memoryTab = wrapper.get('[data-tab="memory"]')
    const panel = wrapper.get('[role="tabpanel"]')

    expect(filesTab.attributes('id')).toBe('inspector-tab-files')
    expect(filesTab.attributes('aria-controls')).toBe('inspector-panel-files')
    expect(panel.attributes('id')).toBe('inspector-panel-files')
    expect(panel.attributes('aria-labelledby')).toBe('inspector-tab-files')

    await filesTab.trigger('keydown', { key: 'ArrowRight' })
    expect(changesTab.attributes('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(changesTab.element)

    await changesTab.trigger('keydown', { key: 'End' })
    expect(memoryTab.attributes('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(memoryTab.element)

    await memoryTab.trigger('keydown', { key: 'Home' })
    expect(filesTab.attributes('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(filesTab.element)

    await filesTab.trigger('keydown', { key: 'ArrowLeft' })
    expect(memoryTab.attributes('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(memoryTab.element)

    wrapper.unmount()
  })
})
