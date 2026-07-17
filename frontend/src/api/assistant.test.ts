import { afterEach, expect, it, vi } from 'vitest'
import { fetchAssistantHome } from './assistant'

afterEach(() => vi.unstubAllGlobals())

it('loads the assistant home with the server session cookie', async () => {
  const body = {
    identity: { mode: 'local', user_id: null, display_name: '本地工作区' },
    knowledge: { status: 'not_configured', source_count: 0, wiki_page_count: 0, last_synced_at: null },
    sessions: { status: 'empty', items: [], total: 0, error: null },
    projects: { status: 'unavailable', items: [], total: 0, error: null },
    proposals: { status: 'empty', memory_pending: 0, wiki_pending: 0, note_pending: 0, error: null },
    suggested_actions: [],
  }
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => body })
  vi.stubGlobal('fetch', fetchMock)

  await expect(fetchAssistantHome()).resolves.toEqual(body)
  expect(fetchMock).toHaveBeenCalledWith(expect.any(URL), { credentials: 'include' })
})

it('maps an expired cloud session to a Chinese error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))

  await expect(fetchAssistantHome()).rejects.toThrow('登录状态已失效')
})
