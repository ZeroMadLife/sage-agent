import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, expect, it } from 'vitest'
import CodingFilesDrawer from './CodingFilesDrawer.vue'

beforeEach(() => setActivePinia(createPinia()))

it('opens workspace files as a temporary dialog and closes on request', async () => {
  const wrapper = mount(CodingFilesDrawer, {
    props: { visible: true },
    global: { stubs: { CodingFileTree: { template: '<button @click="$emit(\'close\')">关闭树</button>' } } },
  })

  expect(wrapper.get('[role="dialog"]').attributes('aria-label')).toBe('工作区文件')
  await wrapper.get('button[aria-label="关闭文件面板"]').trigger('click')
  expect(wrapper.emitted('close')).toHaveLength(1)
})

it('does not occupy the chat surface while hidden', () => {
  const wrapper = mount(CodingFilesDrawer, {
    props: { visible: false },
    global: { stubs: { CodingFileTree: true } },
  })

  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
})
