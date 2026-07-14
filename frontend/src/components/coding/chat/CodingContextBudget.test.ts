import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import CodingContextBudget from './CodingContextBudget.vue'
import { useCodingStore } from '../../../stores/coding'

beforeEach(() => setActivePinia(createPinia()))

function configuredBudget() {
  const store = useCodingStore()
  store.sessionId = 'session-a'
  store.contextSnapshot = {
    configured: true,
    model_id: 'model-a',
    used_tokens: 252_900,
    model_limit_tokens: 1_000_000,
    effective_limit_tokens: 936_000,
    output_reserve_tokens: 64_000,
    usage_ratio: 252_900 / 936_000,
    level: 'normal',
    estimated: false,
    compactable: true,
    active_run_id: null,
    context_operation_active: false,
    checkpoint_id: null,
    resume_status: 'canonical_fallback',
    checkpoint_resume_enabled: true,
    latest_attempt: null,
    stale_started: false,
  }
  store.contextChars = 252_900
  return store
}

it('renders compact used, total and remaining context from the server snapshot', () => {
  configuredBudget()
  const wrapper = mount(CodingContextBudget)

  expect(wrapper.text()).toContain('252.9k / 1.0M · 剩余 747.1k')
  expect(wrapper.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('25')
  expect(wrapper.get('[role="progressbar"]').attributes('aria-label')).toContain('输出预留 64,000')
})

it('hides unconfigured models and sends explicit compaction from the icon control', async () => {
  const store = useCodingStore()
  store.sessionId = 'session-a'
  const wrapper = mount(CodingContextBudget)
  expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)

  configuredBudget()
  store.compactContext = vi.fn().mockResolvedValue(true)
  await nextTick()
  await wrapper.get('button[aria-label="压缩上下文"]').trigger('click')
  expect(store.compactContext).toHaveBeenCalledOnce()
})
