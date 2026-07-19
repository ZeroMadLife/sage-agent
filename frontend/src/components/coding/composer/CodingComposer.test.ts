import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import CodingComposer from './CodingComposer.vue'
import { useCodingStore } from '../../../stores/coding'

beforeEach(() => {
  setActivePinia(createPinia())
})

function mountComposer(props: { density?: 'default' | 'compact' } = {}) {
  const store = useCodingStore()
  store.sessionId = 'c1'
  store.isThinking = false
  store.skills = [
    { name: 'review', description: 'review code', source: 'builtin', argument_hint: '' },
    { name: 'plan', description: 'plan a task', source: 'builtin', argument_hint: '' },
    { name: 'test', description: 'run tests', source: 'project', argument_hint: '' },
  ]
  const wrapper = mount(CodingComposer, { props })
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
  // Menu should be dismissed after a skill is selected.
  expect(wrapper.find('.skill-menu').exists()).toBe(false)
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

  // After escape, typing "/" again should re-show the menu (dismissed reset).
  await textarea(wrapper).setValue('/')
  await nextTick()
  expect(wrapper.find('.skill-menu').exists()).toBe(true)
})

it('does not render the retired context placeholder inside the composer', () => {
  const { wrapper } = mountComposer()

  expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('模型未配置')
})

it('supports a compact density for the chat dock without changing the default composer', () => {
  const { wrapper: compact } = mountComposer({ density: 'compact' })
  const { wrapper: standard } = mountComposer()

  expect(compact.get('.composer').classes()).toContain('compact')
  expect(standard.get('.composer').classes()).toContain('default')
})

it('accepts and focuses an editable task draft through its public handle', async () => {
  const { wrapper } = mountComposer()
  const handle = wrapper.vm as unknown as {
    setInput: (value: string) => void
    focus: () => void
    hasDraft: () => boolean
  }
  const focus = vi.spyOn(textarea(wrapper).element as HTMLTextAreaElement, 'focus')

  handle.setInput('研究当前节点')
  await nextTick()
  handle.focus()

  expect((textarea(wrapper).element as HTMLTextAreaElement).value).toBe('研究当前节点')
  expect(focus).toHaveBeenCalledOnce()
  expect(handle.hasDraft()).toBe(true)
})

it('shows configured context at the top-right and real reasoning controls in the rail', async () => {
  const { wrapper, store } = mountComposer()
  store.models = [{
    id: 'openai:gpt-test', label: 'GPT Test', provider: 'openai',
    context_window_tokens: 128_000, output_reserve_tokens: 16_000,
    context_configured: true, reasoning_modes: ['low', 'high'],
  }]
  store.currentModelId = 'openai:gpt-test'
  store.contextSnapshot = {
    model_id: 'openai:gpt-test', configured: true, used_tokens: 12_000,
    model_limit_tokens: 128_000, effective_limit_tokens: 112_000,
    output_reserve_tokens: 16_000, usage_ratio: 0.1, level: 'normal',
    estimated: false, compactable: true, active_run_id: null,
    context_operation_active: false, checkpoint_id: null,
    resume_status: 'canonical', checkpoint_resume_enabled: true,
    latest_attempt: null, stale_started: false,
  }
  store.changeReasoning = vi.fn().mockResolvedValue(true)
  await nextTick()

  expect(wrapper.find('.composer-meta [role="progressbar"]').exists()).toBe(true)
  expect(wrapper.find('.composer-controls .reasoning-control').exists()).toBe(true)
  expect(wrapper.text()).toContain('12.0k / 128.0k')

  const high = wrapper.findAll('.reasoning-control button').find((button) => button.text() === 'high')
  await high?.trigger('click')
  expect(store.changeReasoning).toHaveBeenCalledWith('high')
})

it('sends the message on enter after a skill has been selected', async () => {
  const { wrapper, store } = mountComposer()
  store.sendMessage = vi.fn().mockReturnValue(true)

  await textarea(wrapper).setValue('/rev')
  await nextTick()

  // First enter selects the skill (menu is dismissed afterwards).
  await textarea(wrapper).trigger('keydown', { key: 'Enter', shiftKey: false })
  expect(store.sendMessage).not.toHaveBeenCalled()
  expect(wrapper.find('.skill-menu').exists()).toBe(false)

  // Second enter should send the message, not re-enter the skill menu branch.
  await textarea(wrapper).trigger('keydown', { key: 'Enter', shiftKey: false })
  expect(store.sendMessage).toHaveBeenCalledTimes(1)
  expect(store.sendMessage).toHaveBeenCalledWith('/review')
  expect((textarea(wrapper).element as HTMLTextAreaElement).value).toBe('')
})

it('re-shows the skill menu after sending a message', async () => {
  const { wrapper, store } = mountComposer()
  store.sendMessage = vi.fn().mockReturnValue(true)

  // Select a skill, then send it.
  await textarea(wrapper).setValue('/rev')
  await nextTick()
  await textarea(wrapper).trigger('keydown', { key: 'Enter', shiftKey: false })
  await textarea(wrapper).trigger('keydown', { key: 'Enter', shiftKey: false })
  expect(store.sendMessage).toHaveBeenCalledTimes(1)

  // Typing "/" again should pop the menu back up.
  await textarea(wrapper).setValue('/')
  await nextTick()
  expect(wrapper.find('.skill-menu').exists()).toBe(true)
  expect(wrapper.findAll('.skill-menu-item')).toHaveLength(3)
})

it('keeps the draft when the transport rejects the message', async () => {
  const { wrapper, store } = mountComposer()
  store.sendMessage = vi.fn().mockReturnValue(false)

  await textarea(wrapper).setValue('连接恢复后继续发送')
  await textarea(wrapper).trigger('keydown', { key: 'Enter', shiftKey: false })

  expect(store.sendMessage).toHaveBeenCalledWith('连接恢复后继续发送')
  expect((textarea(wrapper).element as HTMLTextAreaElement).value).toBe('连接恢复后继续发送')
})

it('selects a skill by mouse click', async () => {
  const { wrapper } = mountComposer()

  await textarea(wrapper).setValue('/')
  await nextTick()

  await wrapper.findAll('.skill-menu-item')[2].trigger('click')

  expect((textarea(wrapper).element as HTMLTextAreaElement).value).toBe('/test ')
})

it('opens the permission drawer and switches the active mode', async () => {
  const { wrapper, store } = mountComposer()
  store.changePermissionMode = vi.fn().mockResolvedValue(true)

  await wrapper.find('.permission-trigger').trigger('click')
  expect(document.body.textContent).toContain('选择 Sage 的执行权限')

  const options = document.body.querySelectorAll('.mode-option')
  const acceptEdits = Array.from(options).find((option) => option.textContent?.includes('接受编辑'))
  expect(acceptEdits).toBeTruthy()
  ;(acceptEdits as HTMLElement).click()
  await nextTick()

  expect(store.changePermissionMode).toHaveBeenCalledWith('accept_edits')
})

it('closes the permission drawer from its close control', async () => {
  const { wrapper } = mountComposer()

  await wrapper.find('.permission-trigger').trigger('click')
  expect(document.body.querySelector('.permission-drawer')).toBeTruthy()

  ;(document.body.querySelector('.drawer-close') as HTMLButtonElement).click()
  await nextTick()

  expect(document.body.querySelector('.permission-drawer')).toBeNull()
})
