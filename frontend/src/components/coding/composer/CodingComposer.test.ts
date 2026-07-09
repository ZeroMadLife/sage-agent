import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import CodingComposer from './CodingComposer.vue'
import { useCodingStore } from '../../../stores/coding'

beforeEach(() => {
  setActivePinia(createPinia())
})

function mountComposer() {
  const store = useCodingStore()
  store.sessionId = 'c1'
  store.isThinking = false
  store.skills = [
    { name: 'review', description: 'review code', source: 'builtin', argument_hint: '' },
    { name: 'plan', description: 'plan a task', source: 'builtin', argument_hint: '' },
    { name: 'test', description: 'run tests', source: 'project', argument_hint: '' },
  ]
  const wrapper = mount(CodingComposer)
  return { wrapper, store }
}

function textarea(wrapper: ReturnType<typeof mount>) {
  return wrapper.find('textarea')
}

it('shows the skill menu when input starts with /', async () => {
  const { wrapper } = mountComposer()

  await textarea(wrapper).setValue('/')
  await nextTick()

  expect(wrapper.find('.skill-menu').exists()).toBe(true)
  expect(wrapper.findAll('.skill-menu-item')).toHaveLength(3)
  expect(wrapper.text()).toContain('/review')
  expect(wrapper.text()).toContain('/plan')
})

it('does not show the skill menu for normal input', async () => {
  const { wrapper } = mountComposer()

  await textarea(wrapper).setValue('hello world')
  await nextTick()

  expect(wrapper.find('.skill-menu').exists()).toBe(false)
})

it('filters skills by the query after the slash', async () => {
  const { wrapper } = mountComposer()

  await textarea(wrapper).setValue('/rev')
  await nextTick()

  const items = wrapper.findAll('.skill-menu-item')
  expect(items).toHaveLength(1)
  expect(items[0].text()).toContain('/review')
})

it('shows an empty hint when no skill matches', async () => {
  const { wrapper } = mountComposer()

  await textarea(wrapper).setValue('/zzzz')
  await nextTick()

  expect(wrapper.find('.skill-menu').exists()).toBe(false)
  expect(wrapper.find('.skill-menu-empty').exists()).toBe(true)
  expect(wrapper.text()).toContain('无匹配 skill')
})

it('selects a skill via enter and fills the input with /name and a trailing space', async () => {
  const { wrapper, store } = mountComposer()
  store.sendMessage = vi.fn()

  await textarea(wrapper).setValue('/rev')
  await nextTick()

  await textarea(wrapper).trigger('keydown', { key: 'Enter', shiftKey: false })

  expect(store.sendMessage).not.toHaveBeenCalled()
  expect((textarea(wrapper).element as HTMLTextAreaElement).value).toBe('/review ')
})

it('moves selection with arrow keys and confirms with enter', async () => {
  const { wrapper } = mountComposer()

  await textarea(wrapper).setValue('/')
  await nextTick()

  expect(wrapper.findAll('.skill-menu-item')[0].classes()).toContain('active')

  await textarea(wrapper).trigger('keydown', { key: 'ArrowDown' })
  await nextTick()
  expect(wrapper.findAll('.skill-menu-item')[1].classes()).toContain('active')

  await textarea(wrapper).trigger('keydown', { key: 'ArrowDown' })
  await nextTick()
  expect(wrapper.findAll('.skill-menu-item')[2].classes()).toContain('active')

  await textarea(wrapper).trigger('keydown', { key: 'ArrowUp' })
  await nextTick()
  expect(wrapper.findAll('.skill-menu-item')[1].classes()).toContain('active')

  await textarea(wrapper).trigger('keydown', { key: 'Enter', shiftKey: false })
  await nextTick()
  expect((textarea(wrapper).element as HTMLTextAreaElement).value).toBe('/plan ')
})

it('closes the menu and clears input on escape', async () => {
  const { wrapper } = mountComposer()

  await textarea(wrapper).setValue('/')
  await nextTick()
  expect(wrapper.find('.skill-menu').exists()).toBe(true)

  await textarea(wrapper).trigger('keydown', { key: 'Escape' })
  await nextTick()

  expect(wrapper.find('.skill-menu').exists()).toBe(false)
  expect((textarea(wrapper).element as HTMLTextAreaElement).value).toBe('')
})

it('selects a skill by mouse click', async () => {
  const { wrapper } = mountComposer()

  await textarea(wrapper).setValue('/')
  await nextTick()

  await wrapper.findAll('.skill-menu-item')[2].trigger('click')

  expect((textarea(wrapper).element as HTMLTextAreaElement).value).toBe('/test ')
})
