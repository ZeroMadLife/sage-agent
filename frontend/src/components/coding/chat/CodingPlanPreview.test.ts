import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CodingPlanPreview from './CodingPlanPreview.vue'
import { useCodingStore } from '../../../stores/coding'

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

it('renders plan markdown content fetched from the api', async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ path: '.coding/plans/x.md', content: '# Plan\n\n- step 1', lines: 2 }),
  })
  vi.stubGlobal('fetch', fetchMock)
  const store = useCodingStore()
  store.sessionId = 'c1'

  const wrapper = mount(CodingPlanPreview, {
    props: {
      planPath: '.coding/plans/x.md',
      topic: 'refactor auth',
      visible: true,
    },
  })

  // Wait for the async load to flush.
  await new Promise((resolve) => setTimeout(resolve, 0))
  await wrapper.vm.$nextTick()

  expect(fetchMock).toHaveBeenCalledWith(expect.any(URL))
  const calledUrl = fetchMock.mock.calls[0][0] as URL
  expect(calledUrl.searchParams.get('path')).toBe('.coding/plans/x.md')

  expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
  expect(wrapper.text()).toContain('refactor auth')
  expect(wrapper.text()).toContain('step 1')
  expect(wrapper.html()).toContain('<h1>Plan</h1>')
})

it('shows an error message when the fetch fails', async () => {
  const fetchMock = vi.fn().mockRejectedValue(new Error('boom'))
  vi.stubGlobal('fetch', fetchMock)
  const store = useCodingStore()
  store.sessionId = 'c1'

  const wrapper = mount(CodingPlanPreview, {
    props: {
      planPath: '.coding/plans/x.md',
      topic: 'topic',
      visible: true,
    },
  })

  await new Promise((resolve) => setTimeout(resolve, 0))
  await wrapper.vm.$nextTick()

  expect(wrapper.text()).toContain('boom')
  expect(wrapper.find('.plan-error').exists()).toBe(true)
})

it('emits close when the close button is clicked', async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ path: '.coding/plans/x.md', content: 'plan', lines: 1 }),
  })
  vi.stubGlobal('fetch', fetchMock)
  const store = useCodingStore()
  store.sessionId = 'c1'

  const wrapper = mount(CodingPlanPreview, {
    props: {
      planPath: '.coding/plans/x.md',
      topic: 'topic',
      visible: true,
    },
  })

  await new Promise((resolve) => setTimeout(resolve, 0))

  await wrapper.find('button.close-btn').trigger('click')

  expect(wrapper.emitted('close')).toHaveLength(1)
})

it('does not fetch when not visible', async () => {
  const fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  const store = useCodingStore()
  store.sessionId = 'c1'

  mount(CodingPlanPreview, {
    props: {
      planPath: '.coding/plans/x.md',
      topic: 'topic',
      visible: false,
    },
  })

  await new Promise((resolve) => setTimeout(resolve, 0))

  expect(fetchMock).not.toHaveBeenCalled()
})
