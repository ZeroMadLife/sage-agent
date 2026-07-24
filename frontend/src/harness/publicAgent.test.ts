import { afterEach, describe, expect, it, vi } from 'vitest'
import { answerPublicProfileQuestion, type PublicAgentStreamEvent } from './publicAgent'

afterEach(() => vi.useRealTimers())

describe('public Agent client', () => {
  it('returns live citations and an immutable package receipt', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      status: 'answered',
      answer: 'Harness 从 timeline 恢复。[E1]',
      citations: [{
        citation_id: 'E1', document_id: 'harness-2', title: 'Harness 2.0',
        url: 'https://sagecompanion.top/#harness', revision: 'r2', excerpt: '公开摘要',
      }],
      receipt: { request_id: 'pub_123', package_revision: '2026-07-22.1', package_digest: 'abc' },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    const result = await answerPublicProfileQuestion('Harness 如何恢复？', { fetcher })

    expect(result.mode).toBe('live')
    expect(result.receipt?.packageRevision).toBe('2026-07-22.1')
    expect(result.sources[0]).toMatchObject({ id: 'harness-2', revision: 'r2' })
    expect(fetcher).toHaveBeenCalledWith('/api/public/v1/ask', expect.objectContaining({
      method: 'POST', credentials: 'omit', cache: 'no-store',
    }))
  })

  it('consumes SSE events split across transport chunks', async () => {
    const encoder = new TextEncoder()
    const payload = [
      'event: stage\ndata: {"stage":"retrieving","label":"检索公开资料"}\n\n',
      'event: stage\ndata: {"stage":"grounding","label":"核对回答依据"}\n\n',
      'event: answer_delta\ndata: {"delta":"Sage 使用 Vue 3、"}\n\n',
      'event: answer_delta\ndata: {"delta":"FastAPI 和 PostgreSQL。[E1]"}\n\n',
      'event: sources\ndata: {"citations":[{"citation_id":"E1","document_id":"sage-architecture","title":"Sage 技术架构","url":"https://github.com/ZeroMadLife/sage-agent#架构","revision":"2026-07-24","excerpt":"公开架构证据"}]}\n\n',
      'event: completed\ndata: {"status":"answered","receipt":{"request_id":"pub_123","package_revision":"2026-07-24.3","package_digest":"abc"},"usage":{"input_tokens":50,"output_tokens":8}}\n\n',
    ].join('')
    const chunks = [payload.slice(0, 43), payload.slice(43, 177), payload.slice(177)]
    const fetcher = vi.fn(async () => new Response(new ReadableStream({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)))
        controller.close()
      },
    }), { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } }))
    const events: PublicAgentStreamEvent[] = []

    const result = await answerPublicProfileQuestion('项目使用了什么技术栈？', {
      fetcher,
      onEvent: (event) => events.push(event),
    })

    expect(events.filter((event) => event.type === 'stage').map((event) => event.label))
      .toEqual(['检索公开资料', '核对回答依据'])
    expect(events.filter((event) => event.type === 'answer_delta').map((event) => event.delta).join(''))
      .toBe('Sage 使用 Vue 3、FastAPI 和 PostgreSQL。[E1]')
    expect(result.mode).toBe('live')
    expect(result.sources[0]).toMatchObject({ id: 'sage-architecture', revision: '2026-07-24' })
    expect(result.receipt?.packageRevision).toBe('2026-07-24.3')
    expect(fetcher).toHaveBeenCalledWith('/api/public/v1/ask', expect.objectContaining({
      headers: expect.objectContaining({ Accept: 'text/event-stream' }),
    }))
  })

  it('makes rate limiting visible and falls back to bounded public copy', async () => {
    const fetcher = vi.fn(async () => new Response('{"detail":"limited"}', {
      status: 429, headers: { 'Retry-After': '60' },
    }))

    const result = await answerPublicProfileQuestion('Sage 是做什么的？', { fetcher })

    expect(result.mode).toBe('fallback')
    expect(result.notice).toContain('60 秒后重试')
    expect(result.answer).toContain('Personal AI Learning Companion')
  })

  it('falls back without fabricating a live receipt when the API is unreachable', async () => {
    const fetcher = vi.fn(async () => { throw new TypeError('network failed') })

    const result = await answerPublicProfileQuestion('Knowledge 是什么？', { fetcher })

    expect(result.mode).toBe('fallback')
    expect(result.notice).toBe('公开问答连接失败')
    expect(result.receipt).toBeUndefined()
  })

  it('keeps the public identity and PublishedPackage boundary in the local fallback', async () => {
    const fetcher = vi.fn(async () => { throw new TypeError('network failed') })

    const result = await answerPublicProfileQuestion('你是谁？', { fetcher })

    expect(result.answer).toContain('受限公开资料助手')
    expect(result.answer).toContain('PublishedPackage')
    expect(result.answer).toContain('不连接私人 Session')
    expect(result.receipt).toBeUndefined()
  })
})
