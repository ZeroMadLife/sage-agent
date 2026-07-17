import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CodingPlanApproval from './CodingPlanApproval.vue'
import { useCodingStore } from '../../../stores/coding'

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function setPlanReview(store: ReturnType<typeof useCodingStore>) {
  store.sessionId = 'c1'
  store.planReview = {
    review_id: 'plan_review_1',
    plan_path: '.coding/plans/xxx-plan.md',
    summary: '# Plan\n\n- step 1\n- step 2',
  }
}

it('renders nothing when there is no plan review', () => {
  const store = useCodingStore()
  store.sessionId = 'c1'

  const wrapper = mount(CodingPlanApproval)

  expect(wrapper.find('.plan-approval').exists()).toBe(false)
})

it('renders the plan path, summary markdown and both action buttons', () => {
  const store = useCodingStore()
  setPlanReview(store)

  const wrapper = mount(CodingPlanApproval)

  expect(wrapper.find('[aria-label="Plan approval"]').exists()).toBe(true)
  expect(wrapper.text()).toContain('.coding/plans/xxx-plan.md')
  expect(wrapper.text()).toContain('step 1')
  expect(wrapper.text()).toContain('step 2')
  expect(wrapper.html()).toContain('<h1>Plan</h1>')
  expect(wrapper.find('button.approve-btn').exists()).toBe(true)
  expect(wrapper.find('button.reject-btn').exists()).toBe(true)
})

it('calls approveCodingPlan when the approve button is clicked', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true })
  vi.stubGlobal('fetch', fetchMock)
  const store = useCodingStore()
  setPlanReview(store)

  const wrapper = mount(CodingPlanApproval)

  await wrapper.find('button.approve-btn').trigger('click')
  await new Promise((resolve) => setTimeout(resolve, 0))

  const calledUrl = fetchMock.mock.calls[0][0] as URL
  expect(calledUrl.pathname).toBe('/api/v1/coding/c1/plan/approve')
  expect(fetchMock.mock.calls[0][1]).toEqual({ credentials: 'include', method: 'POST' })
})

it('calls rejectCodingPlan and clears plan review when the reject button is clicked', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true })
  vi.stubGlobal('fetch', fetchMock)
  const store = useCodingStore()
  setPlanReview(store)

  const wrapper = mount(CodingPlanApproval)

  await wrapper.find('button.reject-btn').trigger('click')
  await new Promise((resolve) => setTimeout(resolve, 0))

  const calledUrl = fetchMock.mock.calls[0][0] as URL
  expect(calledUrl.pathname).toBe('/api/v1/coding/c1/plan/reject')
  expect(fetchMock.mock.calls[0][1]).toEqual({ credentials: 'include', method: 'POST' })
  // Backend stays in plan mode (no runtime_mode_changed), so the store clears
  // planReview locally.
  expect(store.planReview).toBeNull()
})
