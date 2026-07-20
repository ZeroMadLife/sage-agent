import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { useAssistantHomeStore } from '../stores/assistantHome'
import EvolutionView from './EvolutionView.vue'
import PublishingStudioView from './PublishingStudioView.vue'
import PublicProfileView from './PublicProfileView.vue'
import PublicProfileRouteView from './PublicProfileRouteView.vue'

vi.mock('../api/knowledge', () => ({
  fetchKnowledgeGraphInsights: vi.fn().mockRejectedValue(new Error('not connected')),
}))

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/growth', component: EvolutionView },
      { path: '/publishing', component: PublishingStudioView },
      { path: '/public', component: PublicProfileView },
      { path: '/coding', component: { template: '<div />' } },
    ],
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

it('renders growth from real summary counts without inventing a mastery percentage', async () => {
  const router = createTestRouter()
  await router.push('/growth')
  const home = useAssistantHomeStore()
  home.summary = {
    identity: { mode: 'local', user_id: null, display_name: '本地工作区' },
    knowledge: { status: 'ready', source_count: 3, wiki_page_count: 42, last_synced_at: null },
    sessions: { status: 'ready', total: 7, error: null, items: [] },
    projects: { status: 'empty', total: 0, error: null, items: [] },
    proposals: { status: 'ready', memory_pending: 1, wiki_pending: 2, note_pending: 0, error: null },
    suggested_actions: [],
  }
  home.load = vi.fn().mockResolvedValue(undefined)

  const wrapper = mount(EvolutionView, { global: { plugins: [router] } })

  expect(wrapper.text()).toContain('成长记录')
  expect(wrapper.text()).toContain('知识资产42')
  expect(wrapper.text()).toContain('待确认沉淀3')
  expect(wrapper.text()).toContain('对话线程7')
  expect(wrapper.text()).toContain('等待目标能力投影')
  expect(wrapper.text()).toContain('42 个 Wiki 页面可用于目标检索')
  expect(wrapper.text()).not.toContain('%')
  wrapper.unmount()
})

it('keeps publishing local and blocks release until the backend contract exists', async () => {
  const router = createTestRouter()
  await router.push('/publishing')
  const wrapper = mount(PublishingStudioView, { global: { plugins: [router] } })

  expect(wrapper.text()).toContain('发布工作室')
  expect(wrapper.text()).toContain('仅保存在本机')
  expect(wrapper.text()).toContain('等待发布契约')
  expect(wrapper.get('.publish-button').attributes('disabled')).toBeDefined()
  expect(wrapper.get('input[aria-label="文章标题"]').element).toBeInstanceOf(HTMLInputElement)
  wrapper.unmount()
})

it('opens the public Sage preview with source-scoped answers', async () => {
  const router = createTestRouter()
  await router.push('/public')
  const wrapper = mount(PublicProfileView, { global: { plugins: [router] } })

  expect(wrapper.text()).toContain('ZeroMadLife')
  expect(wrapper.text()).toContain('Sage / Personal AI Learning Companion')
  await wrapper.get('.ask-sage').trigger('click')
  expect(wrapper.text()).toContain('公开资料预览')
  expect(wrapper.text()).toContain('只回答这页已经公开的项目、方法和成长记录')
  await wrapper.get('.agent-prompts button').trigger('click')
  await wrapper.get('.agent-form').trigger('submit')
  expect(wrapper.text()).toContain('Sage 是一个 Personal AI Learning Companion')
  expect(wrapper.text()).toContain('回答依据')
  expect(wrapper.get('.agent-source').attributes('data-target')).toBe('work')
  expect(wrapper.text()).not.toContain('/Users/')
  wrapper.unmount()
})

it('keeps internal section navigation inside the hash-router public route', async () => {
  const router = createTestRouter()
  await router.push('/public')
  const wrapper = mount(PublicProfileView, { global: { plugins: [router] } })

  await wrapper.get('[data-section="writing"]').trigger('click')

  expect(router.currentRoute.value.path).toBe('/public')
  expect(wrapper.get('[data-section="writing"]').classes()).toContain('active')
  wrapper.unmount()
})

it('lets a visitor inspect project evidence before leaving the public profile', async () => {
  const router = createTestRouter()
  await router.push('/public')
  const wrapper = mount(PublicProfileView, { global: { plugins: [router] } })

  const firstWork = wrapper.get('[data-work-id="sage"]')
  expect(firstWork.attributes('aria-expanded')).toBe('false')
  await firstWork.trigger('click')

  expect(firstWork.attributes('aria-expanded')).toBe('true')
  expect(wrapper.get('[data-work-evidence="sage"]').text()).toContain('为什么做')
  expect(wrapper.get('[data-work-evidence="sage"]').text()).toContain('怎么判断有效')
  expect(wrapper.get('[data-work-evidence="sage"]').text()).toContain('当前边界')
  expect(wrapper.get('[data-work-evidence="sage"] a').attributes('target')).toBe('_blank')
  wrapper.unmount()
})

it('keeps the local publishing draft preview on the private application route', async () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/public', component: PublicProfileRouteView }],
  })
  await router.push('/public?preview=draft')

  const wrapper = mount(PublicProfileRouteView, { global: { plugins: [router] } })
  await flushPromises()

  expect(wrapper.text()).toContain('PUBLIC DRAFT')
  expect(wrapper.text()).toContain('如何让 Agent Harness 支持可靠恢复')
  wrapper.unmount()
})
