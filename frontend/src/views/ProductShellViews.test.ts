import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { useAssistantHomeStore } from '../stores/assistantHome'
import EvolutionView from './EvolutionView.vue'
import PublishingStudioView from './PublishingStudioView.vue'
import PublicProfileView from './PublicProfileView.vue'

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

it('opens a truthful public Agent draft drawer without generating an answer', async () => {
  const router = createTestRouter()
  await router.push('/public')
  const wrapper = mount(PublicProfileView, { global: { plugins: [router] } })

  expect(wrapper.text()).toContain('持续构建，也持续理解')
  await wrapper.get('.ask-sage').trigger('click')
  expect(wrapper.text()).toContain('公开 Agent 尚未接入')
  expect(wrapper.text()).toContain('不生成虚假回答')
  expect(wrapper.get('.public-agent form button').attributes('disabled')).toBeDefined()
  expect(wrapper.text()).not.toContain('/Users/')
  wrapper.unmount()
})
