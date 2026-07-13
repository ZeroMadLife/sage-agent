import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  approveCodingPlan,
  buildCodingStreamUrl,
  fetchCodingApprovalPending,
  fetchCodingRun,
  fetchCodingRuns,
  fetchCodingSessionMessages,
  fetchCodingTimeline,
  fetchCodingTimelineTail,
  fetchOlderCodingTimeline,
  fetchCodingSessions,
  rejectCodingPlan,
  respondCodingApproval,
  resumeCodingSession,
  startCodingSession,
  stopCodingRun,
  fetchMemoryProposals,
  approveMemoryProposal,
  rejectMemoryProposal,
} from './coding'

describe('coding API client', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('lists pending memory proposals for a session', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ proposals: [{ proposal_id: 'p1', status: 'pending', revision: 2, candidates: [] }] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchMemoryProposals('c1')
    expect(result.proposals[0].proposal_id).toBe('p1')
    const url = fetchMock.mock.calls[0][0] as URL
    expect(url.pathname).toBe('/api/v1/coding/c1/memory/proposals')
    expect(url.searchParams.get('status')).toBe('pending')
  })

  it('approves and rejects memory proposals with a revision precondition', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ proposal_id: 'p1', status: 'approved', revision: 3, candidates: [] }) })
    vi.stubGlobal('fetch', fetchMock)
    await approveMemoryProposal('c1', 'p1', 2)
    await rejectMemoryProposal('c1', 'p2', 4)
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST', body: JSON.stringify({ expected_revision: 2 }) })
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST', body: JSON.stringify({ expected_revision: 4 }) })
  })

  it('maps a memory CAS conflict to a Chinese retryable error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409 }))
    await expect(approveMemoryProposal('c1', 'p1', 2)).rejects.toThrow('记忆候选已发生变化，请刷新后重试')
  })

  it('creates a coding session', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_id: 'c1', workspace_root: '/tmp/repo', permission_mode: 'default' }),
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
    const url = buildCodingStreamUrl('c1', 17)

    expect(url).toContain('/api/v1/coding/c1/stream')
    expect(new URL(url).searchParams.get('after')).toBe('17')
    expect(url.startsWith('ws')).toBe(true)
  })

  it('fetches a cursor-paginated coding timeline', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [], next_cursor: 9, has_more: false, older_cursor: null,
        latest_cursor: 9, active_run: null,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const response = await fetchCodingTimeline('c1', 4, 25)

    const url = fetchMock.mock.calls[0][0] as URL
    expect(url.pathname).toBe('/api/v1/coding/session/c1/timeline')
    expect(url.searchParams.get('after')).toBe('4')
    expect(url.searchParams.get('limit')).toBe('25')
    expect(response.next_cursor).toBe(9)
  })

  it('rejects malformed or cross-session timeline envelopes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [{
          event_id: 'event-1', session_id: 'other', run_id: 'run-1', sequence: 1,
          kind: 'assistant', status: 'completed', timestamp: '2026-07-12T00:00:00Z',
          payload: { type: 'final', content: 'done' },
        }],
        next_cursor: 1, has_more: false, older_cursor: null,
        latest_cursor: 1, active_run: null,
      }),
    }))

    await expect(fetchCodingTimeline('c1')).rejects.toThrow('收到无效的时间线响应')
  })

  it('fetches bounded tail and older timeline pages', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [], next_cursor: 250, has_more: true,
        older_cursor: 151, latest_cursor: 250, active_run: null,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await fetchCodingTimelineTail('c1', 100)
    await fetchOlderCodingTimeline('c1', 151, 100)

    const tailUrl = fetchMock.mock.calls[0][0] as URL
    const olderUrl = fetchMock.mock.calls[1][0] as URL
    expect(tailUrl.searchParams.get('tail')).toBe('true')
    expect(tailUrl.searchParams.get('limit')).toBe('100')
    expect(olderUrl.searchParams.get('before')).toBe('151')
    expect(olderUrl.searchParams.get('limit')).toBe('100')
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

  it('approves a plan via REST', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'approved', mode: 'default' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await approveCodingPlan('c1')

    const calledUrl = fetchMock.mock.calls[0][0] as URL
    expect(calledUrl.pathname).toBe('/api/v1/coding/c1/plan/approve')
    expect(fetchMock.mock.calls[0][1]).toEqual({ method: 'POST' })
    expect(result).toEqual({ status: 'approved', mode: 'default' })
  })

  it('rejects a plan via REST', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)

    await rejectCodingPlan('c1')

    const calledUrl = fetchMock.mock.calls[0][0] as URL
    expect(calledUrl.pathname).toBe('/api/v1/coding/c1/plan/reject')
    expect(fetchMock.mock.calls[0][1]).toEqual({ method: 'POST' })
  })

  it('throws when plan approval fails', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 500 })
    vi.stubGlobal('fetch', fetchMock)

    await expect(approveCodingPlan('c1')).rejects.toThrow('approve plan failed: 500')
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
      json: async () => ({ session_id: 's1', workspace_root: '/tmp/repo', permission_mode: 'auto' }),
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
