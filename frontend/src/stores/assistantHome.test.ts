import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { useAssistantHomeStore } from './assistantHome'

beforeEach(() => setActivePinia(createPinia()))

it('deduplicates concurrent home loads', async () => {
  let resolve!: (value: unknown) => void
  const pending = new Promise((done) => { resolve = done })
  const fetchMock = vi.fn().mockReturnValue(pending)
  vi.stubGlobal('fetch', fetchMock)
  const store = useAssistantHomeStore()

  const first = store.load()
  const second = store.load()
  resolve({
    ok: true,
    json: async () => ({
      identity: { mode: 'local', user_id: null, display_name: '本地工作区' },
      knowledge: { status: 'not_configured', source_count: 0, wiki_page_count: 0, last_synced_at: null },
      sessions: { status: 'empty', items: [], total: 0, error: null },
      projects: { status: 'unavailable', items: [], total: 0, error: null },
      proposals: { status: 'empty', memory_pending: 0, wiki_pending: 0, note_pending: 0, error: null },
      suggested_actions: [],
    }),
  })
  await Promise.all([first, second])

  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(store.summary?.identity.mode).toBe('local')
  vi.unstubAllGlobals()
})

it('keeps an existing summary visible when a forced refresh fails', async () => {
  const store = useAssistantHomeStore()
  store.summary = {
    identity: { mode: 'local', user_id: null, display_name: '本地工作区' },
    knowledge: { status: 'not_configured', source_count: 0, wiki_page_count: 0, last_synced_at: null },
    sessions: { status: 'empty', items: [], total: 0, error: null },
    projects: { status: 'unavailable', items: [], total: 0, error: null },
    proposals: { status: 'empty', memory_pending: 0, wiki_pending: 0, note_pending: 0, error: null },
    suggested_actions: [],
  }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }))

  await store.load(true)

  expect(store.summary.identity.display_name).toBe('本地工作区')
  expect(store.error).toContain('503')
  vi.unstubAllGlobals()
})
