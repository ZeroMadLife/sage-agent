import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { useAssistantHomeStore } from '../stores/assistantHome'
import EvolutionView from './EvolutionView.vue'
import PublishingStudioView from './PublishingStudioView.vue'
import PublicProfileView from './PublicProfileView.vue'
import PublicProfileRouteView from './PublicProfileRouteView.vue'
import PublicHomeView from './public/PublicHomeView.vue'
import NotesListView from './public/NotesListView.vue'
import NoteDetailView from './public/NoteDetailView.vue'

vi.mock('../api/knowledge', () => ({
  fetchKnowledgeGraphInsights: vi.fn().mockRejectedValue(new Error('not connected')),
}))

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/growth', component: EvolutionView },
      { path: '/publishing', component: PublishingStudioView },
      { path: '/', component: PublicHomeView },
      { path: '/public', component: PublicProfileView },
      { path: '/notes', component: NotesListView },
      { path: '/notes/:slug', component: NoteDetailView, props: true },
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
  expect(wrapper.text()).toContain('不是又一个聊天框')
  expect(wrapper.text()).toContain('体系')
  await wrapper.get('[data-action="ask-sage"]').trigger('click')
  expect(wrapper.text()).toContain('公开资料预览')
  expect(wrapper.text()).toContain('只回答已经公开的项目、方法和成长记录')
  await wrapper.get('.agent-prompts button').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('Sage 是一个个人 AI 学习伙伴')
  expect(wrapper.text()).toContain('回答依据')
  expect(wrapper.text()).not.toContain('/Users/')
  wrapper.unmount()
})

it('leads with harness evidence and keeps notes navigable', async () => {
  const router = createTestRouter()
  await router.push('/')
  const wrapper = mount(PublicHomeView, { global: { plugins: [router] } })

  expect(wrapper.text()).toContain('真实工作台，不是概念图')
  expect(wrapper.text()).toContain('GitHub')
  expect(wrapper.text()).toContain('不提供写笔记入口')
  expect(wrapper.find('[data-action="write-note"]').exists()).toBe(false)
  expect(wrapper.find('[data-nav="notes"]').exists()).toBe(true)
  await wrapper.get('[data-nav="notes"]').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/notes')
  wrapper.unmount()
})

it('lists engineering notes and opens markdown detail content', async () => {
  const router = createTestRouter()
  await router.push('/notes')
  const list = mount(NotesListView, { global: { plugins: [router] } })
  expect(list.text()).toContain('只读的公开笔记')
  expect(list.text()).toContain('durable timeline')
  expect(list.text()).toContain('公开站保持只读')
  expect(list.text()).not.toContain('开始写作')
  list.unmount()

  await router.push('/notes/why-durable-timeline')
  const detail = mount(NoteDetailView, {
    props: { slug: 'why-durable-timeline' },
    global: { plugins: [router] },
  })
  expect(detail.text()).toContain('为什么 Harness 需要 durable timeline')
  expect(detail.html()).not.toContain('<script>alert')
  detail.unmount()
})

it('keeps author writing inside the private publishing studio', async () => {
  const router = createTestRouter()
  await router.push('/publishing')
  const wrapper = mount(PublishingStudioView, { global: { plugins: [router] } })
  expect(wrapper.text()).toContain('写作入口（私有）')
  expect(wrapper.text()).toContain('公开站 `/notes` 只读展示')
  expect(wrapper.get('.publish-button').attributes('disabled')).toBeDefined()
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
