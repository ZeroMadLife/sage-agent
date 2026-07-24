import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { expect, it, vi } from 'vitest'
import PublicProfileView from './PublicProfileView.vue'

function mountPublicProfile() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: PublicProfileView }],
  })
  return mount(PublicProfileView, { global: { plugins: [router] } })
}

it('leads with the public product identity, three truthful engineering proofs, and real product screens', async () => {
  const wrapper = mountPublicProfile()

  expect(wrapper.get('h1').text()).toContain('ZeroMadLife / Sage')
  expect(wrapper.text()).toContain('Personal AI Learning Companion')
  expect(wrapper.findAll('[data-hero-evidence]')).toHaveLength(3)
  expect(wrapper.text()).toContain('Goal Contract')
  expect(wrapper.text()).toContain('Harness 2.0')
  expect(wrapper.text()).toContain('Mastery Evidence')
  expect(wrapper.text()).not.toContain('掌握率')
  expect(wrapper.text()).not.toContain('%')
  expect(wrapper.findAll('[data-product-slide]')).toHaveLength(3)
  expect(wrapper.findAll('[data-product-picker]')).toHaveLength(3)
  expect(wrapper.get('[data-product-slide="assistant"]').classes()).toContain('is-active')
  expect(wrapper.get('[data-product-slide="assistant"] img').attributes('src')).toBe('/product/assistant.webp')
  expect(wrapper.findAll('.hero-product__slide img').every((image) => image.attributes('alt')?.includes('不含私人数据'))).toBe(true)

  await wrapper.get('[data-product-picker="knowledge"]').trigger('click')
  expect(wrapper.get('[data-product-slide="knowledge"]').classes()).toContain('is-active')
  expect(wrapper.get('[data-product-picker="knowledge"]').attributes('aria-pressed')).toBe('true')
  expect(wrapper.get('.hero-product').text()).toContain('Knowledge')

  await wrapper.get('[aria-label="下一张产品界面"]').trigger('click')
  expect(wrapper.get('[data-product-slide="harness"]').classes()).toContain('is-active')
  expect(wrapper.get('.hero-product').text()).toContain('Timeline')

  wrapper.unmount()
})

it('labels Ask Sage as a bounded public Agent with transparent fallback', async () => {
  const wrapper = mountPublicProfile()

  await wrapper.get('.ask-sage').trigger('click')

  expect(wrapper.get('.public-agent').attributes('aria-label')).toBe('受限公开资料问答')
  expect(wrapper.text()).toContain('无私人数据')
  expect(wrapper.text()).toContain('失败时透明回退')
  expect(wrapper.text()).toContain('same-origin API')
  expect(wrapper.text()).not.toContain('/Users/')

  wrapper.unmount()
})

it('opens evidence detail without leaving the public surface', async () => {
  const wrapper = mountPublicProfile()
  const mastery = wrapper.get('[data-work-id="mastery"]')

  await mastery.trigger('click')

  expect(mastery.attributes('aria-expanded')).toBe('true')
  expect(wrapper.get('[data-work-evidence="mastery"]').text()).toContain('怎么判断有效')
  expect(wrapper.get('[data-work-evidence="mastery"]').text()).toContain('固定 rubric')

  wrapper.unmount()
})

it('shows a visible public-corpus fallback when the Agent cannot be reached', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline') }))
  const wrapper = mountPublicProfile()

  await wrapper.get('.ask-sage').trigger('click')
  await wrapper.get('.agent-prompts button').trigger('click')
  await wrapper.get('.agent-form').trigger('submit')
  await flushPromises()

  expect(wrapper.text()).toContain('公开问答连接失败')
  expect(wrapper.text()).toContain('以下为本页公开资料回退')
  expect(wrapper.text()).not.toContain('资料包 2026-07-22.1')
  wrapper.unmount()
})

it('sends with Enter while Shift+Enter keeps editing the question', async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({
    status: 'answered',
    answer: '公开回答 [E1]',
    citations: [{
      citation_id: 'E1', document_id: 'sage-overview', title: 'Sage 项目概览',
      url: 'https://github.com/ZeroMadLife/sage-agent', revision: '2026-07-24', excerpt: '公开摘要',
    }],
    receipt: { request_id: 'pub_enter', package_revision: '2026-07-24.2', package_digest: 'abc' },
  }), { headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetcher)
  const wrapper = mountPublicProfile()

  await wrapper.get('.ask-sage').trigger('click')
  const textarea = wrapper.get<HTMLTextAreaElement>('.agent-form textarea')
  await textarea.setValue('请介绍一下这个项目')
  await textarea.trigger('keydown', { key: 'Enter', shiftKey: true })
  expect(fetcher).not.toHaveBeenCalled()

  await textarea.trigger('keydown', { key: 'Enter' })
  await flushPromises()

  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(wrapper.text()).toContain('请介绍一下这个项目')
  expect(wrapper.text()).toContain('公开回答 [E1]')
  wrapper.unmount()
})

it('renders real stream stages and answer deltas before the receipt completes', async () => {
  const encoder = new TextEncoder()
  let streamController: ReadableStreamDefaultController<Uint8Array> | undefined
  vi.stubGlobal('fetch', vi.fn(async () => new Response(new ReadableStream({
    start(controller) { streamController = controller },
  }), { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })))
  const wrapper = mountPublicProfile()

  await wrapper.get('.ask-sage').trigger('click')
  const textarea = wrapper.get<HTMLTextAreaElement>('.agent-form textarea')
  await textarea.setValue('项目使用了什么技术栈？')
  await textarea.trigger('keydown', { key: 'Enter' })
  await flushPromises()

  streamController?.enqueue(encoder.encode(
    'event: stage\ndata: {"stage":"retrieving","label":"检索公开资料"}\n\n',
  ))
  await flushPromises()
  expect(wrapper.text()).toContain('检索公开资料')

  streamController?.enqueue(encoder.encode(
    'event: answer_delta\ndata: {"delta":"前端使用 Vue 3，"}\n\n',
  ))
  await flushPromises()
  expect(wrapper.text()).toContain('前端使用 Vue 3，')

  streamController?.enqueue(encoder.encode([
    'event: sources\ndata: {"citations":[{"citation_id":"E1","document_id":"sage-architecture","title":"Sage 技术栈与系统架构","url":"https://github.com/ZeroMadLife/sage-agent#架构","revision":"2026-07-24","excerpt":"公开架构证据"}]}\n\n',
    'event: completed\ndata: {"status":"answered","receipt":{"request_id":"pub_stream","package_revision":"2026-07-24.2","package_digest":"abc"},"usage":{"input_tokens":50,"output_tokens":8}}\n\n',
  ].join('')))
  streamController?.close()
  await flushPromises()

  expect(wrapper.text()).toContain('E1 · Sage 技术栈与系统架构')
  expect(wrapper.text()).toContain('资料包 2026-07-24.2 · pub_stream')
  expect(textarea.attributes('disabled')).toBeUndefined()
  wrapper.unmount()
})
