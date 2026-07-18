import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it } from 'vitest'
import ChatHarnessLayout from './ChatHarnessLayout.vue'

function mountLayout(props: Record<string, unknown> = {}) {
  return mount(ChatHarnessLayout, {
    props: { surfaceLabel: 'Coding', ...props },
    slots: {
      canvas: '<div data-testid="canvas">canvas</div>',
      chat: '<div data-testid="chat">chat</div>',
      details: '<div data-testid="details">details</div>',
    },
    attachTo: document.body,
  })
}

describe('ChatHarnessLayout', () => {
  beforeEach(() => localStorage.clear())

  it('renders the surface canvas with Chat as the default dock tab', () => {
    const wrapper = mountLayout()

    expect(wrapper.get('[aria-label="Coding 主画布"]').text()).toContain('canvas')
    expect(wrapper.get('[role="tab"][aria-selected="true"]').text()).toContain('对话')
    expect(wrapper.get('[role="tabpanel"]:not([style*="display: none"])').text()).toContain('chat')
    wrapper.unmount()
  })

  it('supports a focused chat dock without exposing an empty details tab', () => {
    const wrapper = mountLayout({ showDetails: false, chatLabel: '主对话' })

    expect(wrapper.get('[role="tab"]').text()).toContain('主对话')
    expect(wrapper.findAll('[role="tab"]')).toHaveLength(1)
    expect(wrapper.find('[data-testid="chat"]').exists()).toBe(true)
    expect(wrapper.find('button[aria-label="打开详情工作台"]').exists()).toBe(false)
    expect(wrapper.get('[aria-label="移动工作台视图"]').findAll('button')).toHaveLength(2)
    wrapper.unmount()
  })

  it('switches tabs with the keyboard and returns focus to the selected tab', async () => {
    const wrapper = mountLayout()
    const chatTab = wrapper.findAll('[role="tab"]')[0]

    await chatTab.trigger('keydown', { key: 'ArrowRight' })
    await nextTick()

    const selected = wrapper.get('[role="tab"][aria-selected="true"]')
    expect(selected.text()).toContain('详情')
    expect(document.activeElement).toBe(selected.element)
    wrapper.unmount()
  })

  it('collapses to an icon rail and restores the Chat dock', async () => {
    const wrapper = mountLayout()

    await wrapper.get('button[aria-label="收起工作台"]').trigger('click')
    expect(wrapper.attributes('data-dock-open')).toBe('false')

    await wrapper.get('button[aria-label="打开对话工作台"]').trigger('click')
    expect(wrapper.attributes('data-dock-open')).toBe('true')
    expect(wrapper.attributes('data-active-tab')).toBe('chat')
    wrapper.unmount()
  })

  it('supports keyboard resizing within the persisted width bounds', async () => {
    const wrapper = mountLayout()
    const separator = wrapper.get('[role="separator"]')

    expect(separator.attributes('aria-valuenow')).toBe('420')
    await separator.trigger('keydown', { key: 'ArrowLeft' })
    expect(separator.attributes('aria-valuenow')).toBe('436')
    await separator.trigger('keydown', { key: 'End' })
    expect(separator.attributes('aria-valuenow')).toBe('520')
    await separator.trigger('keydown', { key: 'Home' })
    expect(separator.attributes('aria-valuenow')).toBe('360')
    expect(localStorage.getItem('sage.harness.dockWidth')).toBe('360')
    wrapper.unmount()
  })

  it('tracks the mobile canvas, chat and details panes without remounting slots', async () => {
    const wrapper = mountLayout()
    const mobileNav = wrapper.get('[aria-label="移动工作台视图"]')

    expect(wrapper.attributes('data-mobile-pane')).toBe('canvas')
    await mobileNav.findAll('button')[1].trigger('click')
    expect(wrapper.attributes('data-mobile-pane')).toBe('chat')
    await mobileNav.findAll('button')[0].trigger('click')
    expect(wrapper.attributes('data-mobile-pane')).toBe('canvas')
    expect(wrapper.find('[data-testid="chat"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="details"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('lets a surface reveal mobile details without remounting the layout', async () => {
    const wrapper = mountLayout()

    ;(wrapper.vm as unknown as { selectTab: (tab: 'chat' | 'details') => void }).selectTab('details')
    await nextTick()

    expect(wrapper.attributes('data-active-tab')).toBe('details')
    expect(wrapper.attributes('data-mobile-pane')).toBe('details')
    expect(wrapper.find('[data-testid="canvas"]').exists()).toBe(true)
    wrapper.unmount()
  })
})
