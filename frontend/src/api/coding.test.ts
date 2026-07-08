import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  buildCodingStreamUrl,
  fetchCodingApprovalPending,
  fetchCodingRun,
  fetchCodingRuns,
  fetchCodingSessionMessages,
  fetchCodingSessions,
  respondCodingApproval,
  resumeCodingSession,
  startCodingSession,
  stopCodingRun,
} from './coding'

describe('coding API client', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('creates a coding session', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_id: 'c1', workspace_root: '/tmp/repo' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const response = await startCodingSession('/tmp/repo')

    expect(response.session_id).toBe('c1')
    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace_root: '/tmp/repo', approval_policy: 'ask' }),
    })
  })

  it('builds a websocket URL for coding streams', () => {
    const url = buildCodingStreamUrl('c1')

    expect(url).toContain('/api/v1/coding/c1/stream')
    expect(url.startsWith('ws')).toBe(true)
  })

  it('fetches pending approval', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        approval_id: 'appr_1',
        session_id: 'c1',
        tool: 'write_file',
        args: { path: 'README.md' },
        description: 'write_file requires approval.',
        pattern_key: 'tool:write_file',
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const response = await fetchCodingApprovalPending('c1')

    expect(response?.approval_id).toBe('appr_1')
    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL))
  })

  it('responds to approval', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)

    await respondCodingApproval('c1', 'appr_1', 'once')

    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id: 'appr_1', choice: 'once' }),
    })
  })

  it('requests coding run stop', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)

    await stopCodingRun('c1')

    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL), { method: 'POST' })
  })

  it('fetches coding run history and detail', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          runs: [
            {
              run_id: 'run_1',
              status: 'completed',
              event_count: 4,
              tool_count: 1,
              error_count: 0,
              last_event_type: 'final',
              started_at: '2026-07-08T10:00:00',
              updated_at: '2026-07-08T10:00:01',
            },
          ],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          run_id: 'run_1',
          events: [{ type: 'final' }],
          timeline: [
            {
              kind: 'final',
              title: 'Final answer',
              detail: 'done',
              status: 'done',
              tool: '',
              timestamp: '',
            },
          ],
        }),
      })
    vi.stubGlobal('fetch', fetchMock)

    const runs = await fetchCodingRuns('c1')
    const detail = await fetchCodingRun('c1', 'run_1')

    expect(runs.runs[0].run_id).toBe('run_1')
    expect(detail.events[0].type).toBe('final')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('fetches coding session history', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        sessions: [
          {
            session_id: 's1',
            title: '读 README',
            workspace_root: '/tmp/repo',
            created_at: '2026-07-08T10:00:00',
            updated_at: '2026-07-08T10:00:01',
            runtime_mode: 'default',
            message_count: 2,
          },
        ],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const response = await fetchCodingSessions()

    expect(response.sessions[0].title).toBe('读 README')
    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL))
  })

  it('resumes a coding session', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_id: 's1', workspace_root: '/tmp/repo' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const response = await resumeCodingSession('s1')

    expect(response.session_id).toBe('s1')
    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL), { method: 'POST' })
  })

  it('fetches coding session messages', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        messages: [
          {
            role: 'user',
            content: '读 README',
            created_at: '2026-07-08T10:00:00',
          },
          {
            role: 'assistant',
            content: 'README 里是 Sage。',
            created_at: '2026-07-08T10:00:01',
          },
        ],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const response = await fetchCodingSessionMessages('s1')

    expect(response.messages[1].content).toBe('README 里是 Sage。')
    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL))
  })
})
