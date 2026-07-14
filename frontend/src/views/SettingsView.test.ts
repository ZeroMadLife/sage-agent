import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import SettingsView from './SettingsView.vue'
import { useCodingStore } from '../stores/coding'

async function mountSettings(path = '/settings/appearance') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/settings/:section', component: SettingsView }] })
  await router.push(path)
  const root = mount({ components: { RouterView }, template: '<RouterView />' }, { global: { plugins: [router] } })
  return { router, root, view: () => root.findComponent(SettingsView) }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

it('keeps settings modules out of the chat route and renders the requested section', async () => {
  const { root, view } = await mountSettings('/settings/skills')
  expect(view().text()).toContain('通过聊天输入框的 /skill 显式调用')
  expect(view().find('.pane-center').exists()).toBe(false)
  root.unmount()
})

it('persists appearance preferences locally', async () => {
  const { root, view } = await mountSettings()
  const theme = view().get('select[aria-label="主题"]')
  await theme.setValue('dark')
  await view().get('input[aria-label="显示工具过程"]').setValue(false)
  expect(localStorage.getItem('sage.ui.theme')).toBe('dark')
  expect(localStorage.getItem('sage.workbench.showToolProcess')).toBe('false')
  root.unmount()
})

it('shows memory review actions and read-only MCP state', async () => {
  const store = useCodingStore()
  store.mcpServers = [{ name: 'docs', transport: 'stdio', status: 'connected' }]
  store.memoryProposals = [{ proposal_id: 'p1', workspace_id: '', session_id: '', run_id: '', reflection_id: '', status: 'pending', projection_status: 'pending', revision: 1, base_revision: 0, candidate_count: 1, candidates: [{ content: '使用 SQLite', topic: '', source: 'dream', source_ref: '', created_at: '' }], created_at: '', updated_at: '' }]
  const { router, root, view } = await mountSettings('/settings/mcp')
  expect(view().text()).toContain('docs')
  expect(view().text()).toContain('只读')
  await router.push('/settings/memory')
  expect(view().text()).toContain('使用 SQLite')
  expect(view().get('button').text()).toContain('返回聊天')
  root.unmount()
})

it('does not archive the active session or leak rejected session mutations', async () => {
  const store = useCodingStore()
  store.sessionId = 'active'
  store.codingSessions = [{ session_id: 'active', title: '当前会话', workspace_root: '', created_at: '', updated_at: '', runtime_mode: 'default', message_count: 0 }]
  store.setSessionArchived = vi.fn().mockRejectedValue(new Error('服务不可用'))
  const { root, view } = await mountSettings('/settings/sessions')
  const archive = view().get('button[title="请在聊天页归档当前会话"]')
  expect(archive.attributes('disabled')).toBeDefined()
  root.unmount()
})

it('uses a full-screen section list on mobile', async () => {
  const { root, view } = await mountSettings('/settings/appearance')
  await view().get('.mobile-section-trigger').trigger('click')
  expect(view().get('[role="dialog"][aria-label="设置分类"]').text()).toContain('运行与任务')
  root.unmount()
})

it('shows sanitized provider status and real usage empty state', async () => {
  const store = useCodingStore()
  store.providerSettings = {
    version: 1,
    default_model: 'openai:gpt-test',
    source: 'project_json',
    editable: true,
    providers: [{
      id: 'openai', label: 'OpenAI', api_mode: 'openai_chat_completions',
      base_url: 'https://api.openai.com/v1', api_key_env: 'OPENAI_API_KEY',
      api_key_configured: true,
      models: [{
        id: 'openai:gpt-test', label: 'GPT Test', context_window_tokens: 128_000,
        output_reserve_tokens: 16_000,
        reasoning: { kind: 'openai_reasoning_effort', modes: ['low', 'high'] },
      }],
    }],
  }
  store.usageSummary = {
    range_days: 30, request_count: 0, session_count: 0,
    input_tokens: null, output_tokens: null, total_tokens: null,
    cache_read_tokens: null, cache_creation_tokens: null,
    cache_hit_ratio: null, cost: null, models: [], daily: [],
  }
  store.loadProviderSettings = vi.fn().mockResolvedValue(undefined)
  store.loadUsage = vi.fn().mockResolvedValue(undefined)
  const { router, root, view } = await mountSettings('/settings/providers')

  expect(view().text()).toContain('环境变量已配置')
  expect(view().text()).toContain('OPENAI_API_KEY')
  expect(view().find('input[type="password"]').exists()).toBe(false)

  await router.push('/settings/usage')
  expect(view().text()).toContain('当前范围内暂无 Provider 用量数据')
  expect(view().text()).toContain('费用')
  root.unmount()
})

it('keeps Provider and usage failures isolated', async () => {
  const store = useCodingStore()
  store.providerSettingsError = 'Provider 加载失败'
  store.usageError = '用量加载失败'
  store.loadProviderSettings = vi.fn().mockResolvedValue(undefined)
  store.loadUsage = vi.fn().mockResolvedValue(undefined)
  const { router, root, view } = await mountSettings('/settings/providers')

  expect(view().text()).toContain('Provider 加载失败')
  expect(view().text()).not.toContain('用量加载失败')

  await router.push('/settings/usage')
  expect(view().text()).toContain('用量加载失败')
  expect(view().text()).not.toContain('Provider 加载失败')
  root.unmount()
})
