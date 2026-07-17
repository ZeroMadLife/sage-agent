import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import ProviderModelSettings from './ProviderModelSettings.vue'
import { useCodingStore } from '../../../stores/coding'
import type { CloudModelProvider } from '../../../types/api'

function accountProvider(): CloudModelProvider {
  return {
    id: 'provider-1',
    name: 'OpenAI',
    api_mode: 'openai_responses',
    base_url: 'https://api.openai.com/v1',
    key_configured: true,
    key_hint: '••••cret',
    status: 'connected',
    last_tested_at: '2026-07-14T08:00:00Z',
    models: [{
      id: 'model-1',
      runtime_id: 'account:provider-1:gpt-5',
      model_id: 'gpt-5',
      display_name: 'GPT-5',
      context_window_tokens: 128_000,
      output_reserve_tokens: 16_000,
      reasoning_supported: true,
    }],
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.unstubAllGlobals()
})

it('shows account Providers and their models in one settings surface', () => {
  const store = useCodingStore()
  store.accountProviderAuthenticated = true
  store.accountProviders = [accountProvider()]
  store.accountDefaultModel = 'account:provider-1:gpt-5'
  store.models = [{
    id: 'account:provider-1:gpt-5', label: 'GPT-5', provider: 'OpenAI',
    context_configured: true, context_window_tokens: 128_000,
    output_reserve_tokens: 16_000, reasoning_modes: ['low', 'high'],
  }]

  const wrapper = mount(ProviderModelSettings)

  expect(wrapper.text()).toContain('OpenAI')
  expect(wrapper.text()).toContain('GPT-5')
  expect(wrapper.text()).toContain('默认模型')
  expect(wrapper.text()).toContain('••••cret')
  expect(wrapper.find('input[type="password"]').exists()).toBe(false)
})

it('keeps the write-only key in the dialog and clears it after saving', async () => {
  const store = useCodingStore()
  store.accountProviderAuthenticated = true
  store.loadModelProviderSettings = vi.fn().mockResolvedValue(true)
  store.bootstrapModelCatalog = vi.fn().mockResolvedValue(undefined)
  const provider = accountProvider()
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => provider })
  vi.stubGlobal('fetch', fetchMock)
  const wrapper = mount(ProviderModelSettings)

  await wrapper.get('button[aria-label="添加 Provider"]').trigger('click')
  await wrapper.get('input[aria-label="Provider 名称"]').setValue('OpenAI')
  await wrapper.get('input[aria-label="Base URL"]').setValue('https://api.openai.com/v1')
  await wrapper.get('input[aria-label="API Key"]').setValue('sk-write-only')
  await wrapper.get('input[aria-label="模型 ID"]').setValue('gpt-5')
  await wrapper.get('input[aria-label="模型显示名称"]').setValue('GPT-5')
  await wrapper.get('button.save-provider').trigger('click')
  await flushPromises()

  const options = fetchMock.mock.calls[0][1] as RequestInit
  expect(JSON.parse(String(options.body))).toMatchObject({ api_key: 'sk-write-only' })
  expect(store.loadModelProviderSettings).toHaveBeenCalledTimes(1)
  expect(store.bootstrapModelCatalog).toHaveBeenCalledWith(true)
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  expect(wrapper.html()).not.toContain('sk-write-only')
  expect(JSON.stringify(store.$state)).not.toContain('sk-write-only')
})

it('opens an existing Provider with an empty key field and merges discovered models', async () => {
  const store = useCodingStore()
  store.accountProviderAuthenticated = true
  store.accountProviders = [accountProvider()]
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ models: ['gpt-5', 'gpt-5-mini'] }),
  }))
  const wrapper = mount(ProviderModelSettings)

  await wrapper.get('button[aria-label="编辑 OpenAI"]').trigger('click')
  expect((wrapper.get('input[aria-label="API Key"]').element as HTMLInputElement).value).toBe('')
  await wrapper.get('button[aria-label="发现模型"]').trigger('click')
  await flushPromises()

  expect(wrapper.findAll('input[aria-label="模型 ID"]')).toHaveLength(2)
  expect(wrapper.text()).toContain('已发现 1 个新模型')
})
