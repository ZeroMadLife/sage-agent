import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useCodingStore } from './coding'
import type { CodingTimelineEvent, MemoryProposal } from '../types/api'

function timelineEvent(
  sessionId: string,
  sequence: number,
  kind: CodingTimelineEvent['kind'],
  payload: Record<string, unknown>,
  runId = 'run-1',
): CodingTimelineEvent {
  return {
    event_id: `${sessionId}-event-${sequence}`,
    session_id: sessionId,
    run_id: runId,
    sequence,
    kind,
    status: kind === 'terminal' ? 'completed' : 'running',
    timestamp: '2026-07-12T00:00:00Z',
    payload,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function memoryProposal(proposalId: string, revision: number): MemoryProposal {
  return {
    proposal_id: proposalId, workspace_id: 'workspace', session_id: 'c1', run_id: '',
    reflection_id: '', status: 'pending', projection_status: 'pending', revision,
    base_revision: 0, candidate_count: 0, candidates: [], created_at: '', updated_at: '',
  }
}

describe('coding store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.removeItem('sage.coding.newRuntimeProfile')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('has correct initial state', () => {
    const store = useCodingStore()
    expect(store.sessionId).toBe('')
    expect(store.messages).toEqual([])
    expect(store.isThinking).toBe(false)
    expect(store.skills).toEqual([])
    expect(store.contextBudget).toBe(0)
    expect(store.sessionsById).toEqual({})
  })

  it('bootstraps the model catalog once before a session exists', async () => {
    const catalog = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const fetchMock = vi.fn().mockReturnValue(catalog.promise)
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()

    const first = store.bootstrapModelCatalog()
    const second = store.bootstrapModelCatalog()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    catalog.resolve({
      ok: true,
      json: async () => ({
        models: [{
          id: 'account:provider-1:model-a', label: 'Model A', provider: 'Account',
          context_configured: true, context_window_tokens: 128_000,
          output_reserve_tokens: 16_000, reasoning_modes: ['low', 'high'],
        }],
        current: 'account:provider-1:model-a',
        reasoning_mode: 'off',
      }),
    })
    await Promise.all([first, second])

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(store.models[0].label).toBe('Model A')
    expect(store.currentModelId).toBe('account:provider-1:model-a')
  })

  it('queues a forced model refresh behind an in-flight bootstrap', async () => {
    const initial = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const refreshed = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const fetchMock = vi.fn()
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(refreshed.promise)
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()

    const bootstrap = store.bootstrapModelCatalog()
    const forced = store.bootstrapModelCatalog(true)
    initial.resolve({ ok: true, json: async () => ({
      models: [], current: '', reasoning_mode: 'off',
    }) })
    await bootstrap
    expect(fetchMock).toHaveBeenCalledTimes(2)
    refreshed.resolve({ ok: true, json: async () => ({
      models: [{
        id: 'account:p1:model-new', label: 'Model New', provider: 'Account',
        context_configured: true, context_window_tokens: 128_000,
        output_reserve_tokens: 16_000, reasoning_modes: [],
      }],
      current: 'account:p1:model-new', reasoning_mode: 'off',
    }) })
    await forced

    expect(store.currentModelId).toBe('account:p1:model-new')
  })

  it('uses only server-advertised runtime profiles for new sessions', async () => {
    localStorage.setItem('sage.coding.newRuntimeProfile', 'deerflow_v2')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        models: [], current: null, reasoning_mode: 'off', runtime_profiles: ['legacy'],
        default_runtime_profile: 'legacy',
      }),
    }))
    const store = useCodingStore()

    await store.bootstrapModelCatalog()

    expect(store.availableRuntimeProfiles).toEqual(['legacy'])
    expect(store.newSessionRuntimeProfile).toBe('legacy')
    expect(localStorage.getItem('sage.coding.newRuntimeProfile')).toBeNull()
    expect(store.setNewSessionRuntimeProfile('deerflow_v2')).toBe(false)
  })

  it('lets the server default Harness 2.0 for a browser with no local preference', async () => {
    class FakeSocket {
      readyState = 0
      onopen: (() => void) | null = null
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null
      send = vi.fn()
      close = vi.fn()
    }
    const empty = {
      items: [], next_cursor: 0, has_more: false, older_cursor: null,
      latest_cursor: 0, active_run: null, models: [], current: null,
      reasoning_mode: 'off', runtime_profiles: ['legacy', 'deerflow_v2'],
      default_runtime_profile: 'deerflow_v2', entries: [], path: '.',
      is_git: false, branch: '', dirty_count: 0, changed_files: [], sessions: [],
      runs: [], configured: false, used_tokens: 0,
    }
    const fetchMock = vi.fn((input: URL | string, init?: RequestInit) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      if (init?.method === 'POST' && url.pathname.endsWith('/coding/session')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 'server-default-v2', workspace_root: '/tmp/repo', workspace_id: 'w1',
            permission_mode: 'default', runtime_profile: 'deerflow_v2',
          }),
        })
      }
      return Promise.resolve({ ok: true, json: async () => empty })
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeSocket)
    const store = useCodingStore()

    await store.initialize()

    const creation = fetchMock.mock.calls.find(([input, init]) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      return init?.method === 'POST' && url.pathname.endsWith('/coding/session')
    })
    expect(creation).toBeDefined()
    expect(JSON.parse(String(creation?.[1]?.body))).toMatchObject({ runtime_profile: null })
    expect(store.newSessionRuntimeProfile).toBe('deerflow_v2')
    expect(store.runtimeProfile).toBe('deerflow_v2')
    expect(localStorage.getItem('sage.coding.newRuntimeProfile')).toBeNull()
    store.disconnect()
  })

  it('creates initial and subsequent sessions with the opted-in Harness 2.0 profile', async () => {
    localStorage.setItem('sage.coding.newRuntimeProfile', 'deerflow_v2')
    class FakeSocket {
      readyState = 0
      onopen: (() => void) | null = null
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null
      send = vi.fn()
      close = vi.fn()
    }
    const empty = {
      items: [], next_cursor: 0, has_more: false, older_cursor: null,
      latest_cursor: 0, active_run: null, models: [], current: null,
      reasoning_mode: 'off', runtime_profiles: ['legacy', 'deerflow_v2'],
      default_runtime_profile: 'deerflow_v2',
      entries: [], path: '.', is_git: false, branch: '', dirty_count: 0,
      changed_files: [], sessions: [], runs: [], configured: false, used_tokens: 0,
    }
    const fetchMock = vi.fn((input: URL | string, init?: RequestInit) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      if (init?.method === 'POST' && url.pathname.endsWith('/coding/session')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 'v2-session', workspace_root: '/tmp/repo', workspace_id: 'w1',
            permission_mode: 'default', runtime_profile: 'deerflow_v2',
          }),
        })
      }
      return Promise.resolve({ ok: true, json: async () => empty })
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeSocket)
    const store = useCodingStore()

    await store.initialize()
    await store.startNewSession()

    const creations = fetchMock.mock.calls.filter(([input, init]) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      return init?.method === 'POST' && url.pathname.endsWith('/coding/session')
    })
    expect(creations).toHaveLength(2)
    for (const creation of creations) {
      expect(JSON.parse(String(creation[1]?.body))).toMatchObject({
        runtime_profile: 'deerflow_v2',
      })
    }
    expect(store.runtimeProfile).toBe('deerflow_v2')
    store.disconnect()
  })

  it('keeps the latest usage range when responses finish out of order', async () => {
    const sevenDays = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const thirtyDays = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    vi.stubGlobal('fetch', vi.fn((input: URL | string) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      return url.searchParams.get('range') === '7d' ? sevenDays.promise : thirtyDays.promise
    }))
    const store = useCodingStore()

    const olderRequest = store.loadUsage('7d')
    const latestRequest = store.loadUsage('30d')
    thirtyDays.resolve({ ok: true, json: async () => ({
      range_days: 30, request_count: 2, session_count: 1,
      input_tokens: 10, output_tokens: 4, total_tokens: 14,
      cache_read_tokens: null, cache_creation_tokens: null,
      cache_hit_ratio: null, cost: null, models: [], daily: [],
    }) })
    await latestRequest
    sevenDays.resolve({ ok: true, json: async () => ({
      range_days: 7, request_count: 1, session_count: 1,
      input_tokens: 3, output_tokens: 1, total_tokens: 4,
      cache_read_tokens: null, cache_creation_tokens: null,
      cache_hit_ratio: null, cost: null, models: [], daily: [],
    }) })
    await olderRequest

    expect(store.usageRange).toBe('30d')
    expect(store.usageSummary?.range_days).toBe(30)
    expect(store.usageSummary?.request_count).toBe(2)
  })

  it('keeps A and B timelines isolated when an A event arrives late', () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.handleTimelineEvent('session-a', timelineEvent(
      'session-a', 1, 'user', { type: 'user', content: 'A 的问题' },
    ))
    store.sessionId = 'session-b'
    store.handleTimelineEvent('session-b', timelineEvent(
      'session-b', 1, 'user', { type: 'user', content: 'B 的问题' },
    ))
    store.handleTimelineEvent('session-a', timelineEvent(
      'session-a', 2, 'assistant', { type: 'final', content: 'A 的回答' },
    ))

    expect(store.messages.map((message) => message.content)).toEqual(['B 的问题'])
    expect(store.sessionsById['session-a'].messages.map((message) => message.content)).toEqual([
      'A 的问题',
      'A 的回答',
    ])
    store.sessionId = 'session-a'
    expect(store.messages.map((message) => message.content)).toEqual(['A 的问题', 'A 的回答'])
  })

  it('deduplicates the same event from history and realtime ingestion', () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    const event = timelineEvent(
      'session-a', 1, 'assistant', { type: 'text_delta', delta: '一次' },
    )

    store.mergeTimelinePage('session-a', [event], { next_cursor: 1, has_more: false })
    store.handleTimelineEvent('session-a', event)

    expect(store.sessionsById['session-a'].timeline).toHaveLength(1)
    expect(store.messages[0].content).toBe('一次')
  })

  it('marks a run active from the persisted system run_started envelope', () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'

    store.handleTimelineEvent('session-a', timelineEvent(
      'session-a', 1, 'system', { event: 'run_started' }, 'run-new',
    ))

    expect(store.activeRun).toEqual({ run_id: 'run-new', status: 'running' })
    expect(store.isThinking).toBe(true)
  })

  it('does not erase REST pagination state when a realtime event arrives', () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.mergeTimelinePage('session-a', [], { next_cursor: 0, has_more: true })

    store.handleTimelineEvent('session-a', timelineEvent(
      'session-a', 1, 'user', { type: 'user', content: '实时消息' },
    ))

    expect(store.timelineHasMore).toBe(true)
  })

  it('merges paginated history in sequence order even when older data arrives later', () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.mergeTimelinePage(
      'session-a',
      [timelineEvent('session-a', 3, 'assistant', { type: 'final', content: '完成' })],
      { next_cursor: 3, has_more: false },
    )
    store.mergeTimelinePage(
      'session-a',
      [
        timelineEvent('session-a', 1, 'user', { type: 'user', content: '问题' }),
        timelineEvent('session-a', 2, 'assistant', { type: 'text_delta', delta: '过程' }),
      ],
      { next_cursor: 3, has_more: false },
    )

    expect(store.sessionsById['session-a'].timeline.map((event) => event.sequence)).toEqual([1, 2, 3])
    expect(store.messages.map((message) => message.content)).toEqual(['问题', '完成'])
  })

  it('loads timeline state and preserves a server-owned active run', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [timelineEvent('session-a', 1, 'user', { type: 'user', content: '运行中' })],
        next_cursor: 1,
        has_more: false,
        older_cursor: null,
        latest_cursor: 1,
        active_run: { run_id: 'run-1', status: 'running' },
      }),
    }))
    const store = useCodingStore()
    store.sessionId = 'session-a'

    await store.loadTimeline('session-a')

    expect(store.activeRun).toEqual({ run_id: 'run-1', status: 'running' })
    expect(store.isThinking).toBe(true)
    expect(store.timelineCursor).toBe(1)
  })

  it('replays every missed timeline page from the initialized cursor before reconnecting', async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) => timelineEvent(
      'session-a', index + 2, 'tool', { type: 'tool_result', tool: 'read_file', args: {}, content: String(index) }, 'run-a',
    ))
    const terminal = timelineEvent(
      'session-a', 102, 'terminal', { event: 'run_completed' }, 'run-a',
    )
    const fetchMock = vi.fn((input: URL | string) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      if (url.pathname.endsWith('/timeline')) {
        const after = url.searchParams.get('after')
        if (after === '1') return Promise.resolve({ ok: true, json: async () => ({
          items: firstPage, next_cursor: 101, has_more: true,
          older_cursor: null, latest_cursor: 102, active_run: { run_id: 'run-a', status: 'running' },
        }) })
        if (after === '101') return Promise.resolve({ ok: true, json: async () => ({
          items: [terminal], next_cursor: 102, has_more: false,
          older_cursor: null, latest_cursor: 102, active_run: null,
        }) })
      }
      if (url.pathname.endsWith('/messages')) {
        return Promise.resolve({ ok: true, json: async () => ({ messages: [] }) })
      }
      if (url.pathname.endsWith('/sessions')) {
        return Promise.resolve({ ok: true, json: async () => ({ sessions: [] }) })
      }
      if (url.pathname.endsWith('/runs')) {
        return Promise.resolve({ ok: true, json: async () => ({ runs: [] }) })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.mergeTimelinePage('session-a', [timelineEvent(
      'session-a', 1, 'user', { type: 'user', content: '继续运行' }, 'run-a',
    )], { next_cursor: 1, latest_cursor: 1, has_more: false })
    store.sessionsById['session-a'].timelineInitialized = true

    await store.loadTimeline('session-a')
    store.handleTimelineEvent('session-a', terminal)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(fetchMock.mock.calls.slice(0, 2).map(([input]) => {
      const url = input as URL
      return { after: url.searchParams.get('after'), tail: url.searchParams.get('tail') }
    })).toEqual([
      { after: '1', tail: null },
      { after: '101', tail: null },
    ])
    expect(store.timeline.map((event) => event.sequence)).toEqual(
      Array.from({ length: 102 }, (_, index) => index + 1),
    )
    expect(store.timeline.filter((event) => event.event_id === terminal.event_id)).toHaveLength(1)
    expect(store.timelineCursor).toBe(102)
  })

  it('loads the latest bounded page and prepends older history', async () => {
    const tail = Array.from({ length: 100 }, (_, index) => timelineEvent(
      'session-a', index + 151, 'system', { type: 'model_requested' }, `run-${index + 151}`,
    ))
    const older = Array.from({ length: 100 }, (_, index) => timelineEvent(
      'session-a', index + 51, 'system', { type: 'model_requested' }, `run-${index + 51}`,
    ))
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        items: tail, next_cursor: 250, has_more: true,
        older_cursor: 151, latest_cursor: 250, active_run: null,
      }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ messages: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        items: older, next_cursor: 250, has_more: true,
        older_cursor: 51, latest_cursor: 260, active_run: null,
      }) })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'session-a'

    await store.loadTimeline('session-a')
    await store.loadOlderTimeline('session-a')

    expect(store.timeline).toHaveLength(100)
    expect(store.timeline[0].sequence).toBe(151)
    expect(store.olderTimeline).toHaveLength(100)
    expect(store.visibleTimeline).toHaveLength(200)
    expect(store.visibleTimeline[0].sequence).toBe(51)
    expect(store.visibleTimeline.at(-1)?.sequence).toBe(250)
    expect(store.olderCursor).toBe(51)
    expect(store.timelineCursor).toBe(250)
    const firstUrl = fetchMock.mock.calls[0][0] as URL
    const secondUrl = fetchMock.mock.calls[2][0] as URL
    expect(firstUrl.searchParams.get('tail')).toBe('true')
    expect(secondUrl.searchParams.get('before')).toBe('151')
  })

  it('keeps the automatically resident timeline window bounded', () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    const events = Array.from({ length: 2_100 }, (_, index) => timelineEvent(
      'session-a', index + 1, 'system', { type: 'model_requested' }, `run-${index + 1}`,
    ))

    store.mergeTimelinePage('session-a', events, {
      next_cursor: 2_100,
      latest_cursor: 2_100,
      has_more: false,
    }, 'history')

    expect(store.timeline).toHaveLength(2_000)
    expect(store.timeline[0].sequence).toBe(101)
    expect(store.timeline.at(-1)?.sequence).toBe(2_100)
  })

  it('keeps more than twenty older pages contiguous and separately bounded', () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    const recent = Array.from({ length: 100 }, (_, index) => timelineEvent(
      'session-a', index + 2_101, 'system', { type: 'model_requested' }, `run-${index + 2_101}`,
    ))
    const older = Array.from({ length: 2_100 }, (_, index) => timelineEvent(
      'session-a', index + 1, 'system', { type: 'model_requested' }, `run-${index + 1}`,
    ))
    store.mergeTimelinePage('session-a', recent, {
      next_cursor: 2_200, latest_cursor: 2_200, older_cursor: 2_101,
    })
    store.mergeTimelinePage('session-a', older, {
      next_cursor: 2_200, latest_cursor: 2_200, older_cursor: 1,
    }, 'older')

    expect(store.olderTimeline).toHaveLength(2_100)
    expect(store.visibleTimeline.map((item) => item.sequence)).toEqual(
      Array.from({ length: 2_200 }, (_, index) => index + 1),
    )
    expect(store.timelineCursor).toBe(2_200)
  })

  it('loads legacy messages for an empty timeline and keeps them with new events', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        items: [], next_cursor: 0, has_more: false,
        older_cursor: null, latest_cursor: 0, active_run: null,
      }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        messages: [{ role: 'assistant', content: '旧回答', created_at: '' }],
      }) })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'session-a'

    await store.loadTimeline('session-a')
    store.handleTimelineEvent('session-a', timelineEvent(
      'session-a', 1, 'user', { type: 'user', content: '新问题' }, 'run-new',
    ))
    store.handleTimelineEvent('session-a', timelineEvent(
      'session-a', 2, 'assistant', { type: 'final', content: '新回答' }, 'run-new',
    ))

    expect(store.messages.map((message) => message.content)).toEqual(['旧回答', '新问题', '新回答'])
    expect(store.sessionsById['session-a'].legacyMessages).toHaveLength(1)
  })

  it('keeps only the legacy prefix when transcript already includes timeline messages', async () => {
    const events = [
      timelineEvent('session-a', 1, 'user', { type: 'user', content: '新问题' }, 'run-new'),
      timelineEvent('session-a', 2, 'assistant', { type: 'final', content: '新回答' }, 'run-new'),
    ]
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        items: events, next_cursor: 2, has_more: false,
        older_cursor: null, latest_cursor: 2, active_run: null,
      }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ messages: [
        { role: 'assistant', content: '旧回答', created_at: '' },
        { role: 'user', content: '新问题', created_at: '' },
        { role: 'assistant', content: '新回答', created_at: '' },
      ] }) })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'session-a'

    await store.loadTimeline('session-a')

    expect(store.messages.map((message) => message.content)).toEqual(['旧回答', '新问题', '新回答'])
    expect(store.sessionsById['session-a'].legacyMessages.map((message) => message.content)).toEqual(['旧回答'])
  })

  it('deduplicates a partial assistant projection against completed transcript text', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        items: [timelineEvent('session-a', 1, 'user', { type: 'user', content: '新问题' }),
          timelineEvent('session-a', 2, 'assistant', { type: 'text_delta', delta: '完成的一部' })],
        next_cursor: 2, has_more: false, older_cursor: null, latest_cursor: 2, active_run: null,
      }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ messages: [
        { role: 'assistant', content: '旧回答', created_at: '' },
        { role: 'user', content: '新问题', created_at: '' },
        { role: 'assistant', content: '完成的一部分', created_at: '' },
      ] }) })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'session-a'

    await store.loadTimeline('session-a')

    expect(store.messages.map((message) => message.content)).toEqual(['旧回答', '新问题', '完成的一部'])
  })

  it('does not execute live network side effects while replaying history', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.mergeTimelinePage('session-a', [
      timelineEvent('session-a', 1, 'tool', {
        type: 'tool_result', tool: 'write_file', args: { path: 'a.txt' },
        content: 'ok', is_error: false,
      }),
      timelineEvent('session-a', 2, 'memory', {
        type: 'memory_proposal_ready', proposal_id: 'proposal-1', session_id: 'session-a',
        run_id: 'run-1', reflection_id: 'reflection-1', candidate_count: 1, base_revision: 0,
      }),
      timelineEvent('session-a', 3, 'run', { type: 'turn_finished' }),
    ], { next_cursor: 3, has_more: false }, 'history')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('performs terminal live cleanup once and clears pending approval', async () => {
    const fetchMock = vi.fn().mockImplementation((input: URL | string) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      if (url.pathname.endsWith('/sessions')) {
        return Promise.resolve({ ok: true, json: async () => ({ sessions: [] }) })
      }
      return Promise.resolve({ ok: true, json: async () => ({ runs: [] }) })
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.handleTimelineEvent('session-a', timelineEvent('session-a', 1, 'approval', {
      type: 'approval_required', approval_id: 'approval-1', tool: 'write_file',
      args: { path: 'a.txt' }, description: '确认', pattern_key: 'tool:write_file',
    }))
    store.handleTimelineEvent('session-a', timelineEvent(
      'session-a', 2, 'terminal', { event: 'run_cancelled' },
    ))
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(store.pendingApproval).toBeNull()
    expect(fetchMock.mock.calls.filter(([input]) => (input as URL).pathname.endsWith('/sessions'))).toHaveLength(1)
    expect(fetchMock.mock.calls.filter(([input]) => (input as URL).pathname.endsWith('/runs'))).toHaveLength(1)
  })

  it('enriches a live approval without doing so during history replay', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ entries: [], content: '' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'session-a'

    store.handleTimelineEvent('session-a', timelineEvent('session-a', 1, 'approval', {
      type: 'approval_required', approval_id: 'approval-1', tool: 'write_file',
      args: { path: 'new.txt', content: 'new' }, description: '确认', pattern_key: 'tool:write_file',
    }))
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(store.pendingApproval?.diff_preview).toEqual([{ type: 'add', text: 'new' }])
    expect(fetchMock).toHaveBeenCalledTimes(1)
    store.disconnect()
  })

  it('writes a delayed approval preview back to its source session only', async () => {
    const listing = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(listing.promise))
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.handleTimelineEvent('session-a', timelineEvent('session-a', 1, 'approval', {
      type: 'approval_required', approval_id: 'approval-a', tool: 'write_file',
      args: { path: 'a.txt', content: 'A' }, description: '确认', pattern_key: 'tool:write_file',
    }, 'run-a'))
    store.sessionId = 'session-b'
    store.pendingApproval = {
      approval_id: 'approval-b', session_id: 'session-b', tool: 'write_file',
      args: {}, description: '', pattern_key: 'tool:write_file',
    }
    listing.resolve({ ok: true, json: async () => ({ entries: [] }) })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(store.sessionsById['session-a'].pendingApproval?.diff_preview).toEqual([
      { type: 'add', text: 'A' },
    ])
    expect(store.pendingApproval?.diff_preview).toBeUndefined()
    store.disconnect()
  })

  it('writes a delayed approval poll back to its source session only', async () => {
    vi.useFakeTimers()
    const polling = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(polling.promise))
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.handleTimelineEvent('session-a', timelineEvent('session-a', 1, 'approval', {
      type: 'approval_required', approval_id: 'approval-a', tool: 'write_file',
      args: {}, description: '确认', pattern_key: 'tool:write_file',
    }, 'run-a'))
    vi.advanceTimersByTime(1_500)
    store.sessionId = 'session-b'
    store.pendingApproval = {
      approval_id: 'approval-b', session_id: 'session-b', tool: 'write_file',
      args: {}, description: '', pattern_key: 'tool:write_file',
    }
    polling.resolve({ ok: true, json: async () => null })
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    expect(store.sessionsById['session-a'].pendingApproval).toBeNull()
    expect(store.pendingApproval?.approval_id).toBe('approval-b')
    store.disconnect()
    vi.useRealTimers()
  })

  it('does not clear another run approval or polling on a rejected-run terminal', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => null })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.handleTimelineEvent('session-a', timelineEvent('session-a', 1, 'approval', {
      type: 'approval_required', approval_id: 'approval-a', tool: 'write_file',
      args: {}, description: '确认', pattern_key: 'tool:write_file',
    }, 'run-a'))
    store.handleTimelineEvent('session-a', timelineEvent(
      'session-a', 2, 'terminal', { event: 'input_rejected' }, 'run-rejected',
    ))
    vi.advanceTimersByTime(1_500)
    await Promise.resolve()

    expect(store.pendingApproval?.approval_id).toBe('approval-a')
    expect(fetchMock).toHaveBeenCalled()
    store.disconnect()
    vi.useRealTimers()
  })

  it('does not let older pages roll back live context, runtime or errors', () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.handleTimelineEvent('session-a', timelineEvent('session-a', 100, 'context', {
      type: 'context_usage_updated', session_id: 'session-a', used_tokens: 100,
      model_limit_tokens: 1_000, output_reserve_tokens: 100, effective_limit_tokens: 900,
      usage_ratio: 0.11, level: 'normal', estimated: false, compactable: true,
    }))
    store.handleTimelineEvent('session-a', timelineEvent('session-a', 101, 'system', {
      type: 'runtime_mode_changed', mode: 'plan', topic: '最新计划', plan_path: 'new.md',
    }))
    store.errorMessage = '最新错误'

    store.mergeTimelinePage('session-a', [
      timelineEvent('session-a', 1, 'context', {
        type: 'context_usage_updated', session_id: 'session-a', used_tokens: 10,
        model_limit_tokens: 1_000, output_reserve_tokens: 100, effective_limit_tokens: 900,
        usage_ratio: 0.01, level: 'normal', estimated: false, compactable: true,
      }),
      timelineEvent('session-a', 2, 'system', {
        type: 'runtime_mode_changed', mode: 'default', topic: '旧计划', plan_path: 'old.md',
      }),
    ], { next_cursor: 101, older_cursor: null, latest_cursor: 101 }, 'older')

    expect(store.contextChars).toBe(100)
    expect(store.runtimeMode).toBe('plan')
    expect(store.planTopic).toBe('最新计划')
    expect(store.errorMessage).toBe('最新错误')
  })

  it('keeps approval completion scoped to the session that initiated it', async () => {
    const response = deferred<{ ok: boolean }>()
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(response.promise))
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.pendingApproval = {
      approval_id: 'approval-a', session_id: 'session-a', tool: 'write_file',
      args: {}, description: '', pattern_key: 'tool:write_file',
    }
    const approval = store.respondApproval('once')
    store.sessionId = 'session-b'
    store.pendingApproval = {
      approval_id: 'approval-b', session_id: 'session-b', tool: 'write_file',
      args: {}, description: '', pattern_key: 'tool:write_file',
    }
    response.resolve({ ok: true })
    await approval

    expect(store.sessionsById['session-a'].pendingApproval).toBeNull()
    expect(store.sessionsById['session-a'].approvalBusy).toBe(false)
    expect(store.pendingApproval?.approval_id).toBe('approval-b')
    expect(store.approvalBusy).toBe(false)
  })

  it('keeps stop completion scoped to the session that initiated it', async () => {
    const response = deferred<{ ok: boolean }>()
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(response.promise))
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.activeRun = { run_id: 'run-a', status: 'running' }
    store.isThinking = true
    store.pendingApproval = {
      approval_id: 'approval-a', session_id: 'session-a', tool: 'write_file',
      args: {}, description: '', pattern_key: 'tool:write_file',
    }
    const stopping = store.stopCurrentRun()
    store.sessionId = 'session-b'
    store.pendingApproval = {
      approval_id: 'approval-b', session_id: 'session-b', tool: 'write_file',
      args: {}, description: '', pattern_key: 'tool:write_file',
    }
    response.resolve({ ok: true })
    await stopping

    expect(store.sessionsById['session-a'].pendingApproval).toBeNull()
    expect(store.pendingApproval?.approval_id).toBe('approval-b')
  })

  it('switches back to a known active session without replacing its server runtime', async () => {
    class FakeSocket {
      readyState = 1
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null
      send = vi.fn()
      close = vi.fn()
    }
    vi.stubGlobal('WebSocket', FakeSocket)
    const fetchMock = vi.fn().mockImplementation((input: URL | string) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      if (url.pathname.includes('/resume')) {
        return Promise.resolve({ ok: true, json: async () => ({ session_id: 'session-a', workspace_root: '/workspace/a', permission_mode: 'accept_edits' }) })
      }
      if (url.pathname.endsWith('/timeline')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [], next_cursor: 2, has_more: false, active_run: { run_id: 'run-a', status: 'running' } }),
        })
      }
      if (url.pathname.endsWith('/context')) {
        return Promise.resolve({ ok: true, json: async () => ({ configured: false, used_tokens: 0 }) })
      }
      if (url.pathname.endsWith('/files')) {
        return Promise.resolve({ ok: true, json: async () => ({ path: '.', entries: [] }) })
      }
      if (url.pathname.endsWith('/git/status')) {
        return Promise.resolve({ ok: true, json: async () => ({ is_git: false, branch: '', dirty_count: 0, changed_files: [] }) })
      }
      return Promise.resolve({ ok: true, json: async () => ({ sessions: [], runs: [], proposals: [] }) })
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.workspaceRoot = '/workspace/a'
    store.permissionMode = 'accept_edits'
    store.handleTimelineEvent('session-a', timelineEvent('session-a', 1, 'user', { type: 'user', content: 'A' }, 'run-a'))
    store.activeRun = { run_id: 'run-a', status: 'running' }
    store.sessionsById['session-a'].timelineInitialized = true
    store.sessionId = 'session-b'
    store.workspaceRoot = '/workspace/b'

    await store.selectSession('session-a')

    expect(store.workspaceRoot).toBe('/workspace/a')
    expect(store.permissionMode).toBe('accept_edits')
    expect(store.activeRun?.run_id).toBe('run-a')
    expect(fetchMock.mock.calls.some(([input, init]) => {
      const url = input as URL
      return init?.method === 'POST' && url.pathname.includes('/session-a/resume')
    })).toBe(true)
    store.disconnect()
  })

  it('loads pending memory proposals and ignores an older generation', async () => {
    let resolveFirst: ((value: unknown) => void) | undefined
    const fetchMock = vi.fn()
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ proposals: [{ proposal_id: 'new', status: 'pending', revision: 0, candidates: [] }] }) })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    const older = store.loadMemoryProposals()
    await store.loadMemoryProposals()
    resolveFirst?.({ ok: true, json: async () => ({ proposals: [{ proposal_id: 'old', status: 'pending', revision: 0, candidates: [] }] }) })
    await older
    expect(store.memoryProposals.map((proposal) => proposal.proposal_id)).toEqual(['new'])
  })

  it('refreshes pending proposals after a proposal-ready event', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ proposals: [{ proposal_id: 'p1', status: 'pending', revision: 0, candidates: [] }] }) })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.handleServerEvent({ type: 'memory_proposal_ready', session_id: 'c1', run_id: 'run_1', proposal_id: 'p1', reflection_id: 'r1', candidate_count: 1, base_revision: 0 } as never)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(store.memoryProposals[0].proposal_id).toBe('p1')
  })

  it('keeps a proposal pending and shows a Chinese CAS error on conflict', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409 }))
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.memoryProposals = [memoryProposal('p1', 2)]
    await store.approveMemoryProposal('p1', 2)
    expect(store.memoryProposals).toHaveLength(1)
    expect(store.memoryProposalError).toBe('记忆候选已发生变化，请刷新后重试')
    expect(store.memoryProposalBusy).toEqual({})
  })

  it('keeps proposal action state isolated from a concurrent list refresh', async () => {
    let resolveApprove: ((value: unknown) => void) | undefined
    const fetchMock = vi.fn()
      .mockReturnValueOnce(new Promise((resolve) => { resolveApprove = resolve }))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ proposals: [{ proposal_id: 'p1', status: 'pending', revision: 2, candidates: [] }] }) })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.memoryProposals = [memoryProposal('p1', 2)]
    const approval = store.approveMemoryProposal('p1', 2)
    await store.loadMemoryProposals()
    resolveApprove?.({ ok: true, json: async () => ({ proposal_id: 'p1', status: 'approved', revision: 3, candidates: [] }) })
    await approval
    expect(store.memoryProposalBusy).toEqual({})
    expect(store.memoryProposals).toEqual([])
  })

  it('finishes a memory transition in its source session after switching away', async () => {
    const response = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(response.promise))
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.memoryProposals = [memoryProposal('proposal-a', 2)]
    const transition = store.approveMemoryProposal('proposal-a', 2)
    store.sessionId = 'session-b'
    store.memoryProposals = [memoryProposal('proposal-b', 1)]
    response.resolve({ ok: true, json: async () => memoryProposal('proposal-a', 3) })
    await transition

    expect(store.sessionsById['session-a'].memoryProposals).toEqual([])
    expect(store.sessionsById['session-a'].memoryProposalBusy).toEqual({})
    expect(store.memoryProposals[0].proposal_id).toBe('proposal-b')
  })

  it('does not let an older pending list resurrect an approved proposal', async () => {
    let resolveList: ((value: unknown) => void) | undefined
    let resolveApprove: ((value: unknown) => void) | undefined
    const fetchMock = vi.fn()
      .mockReturnValueOnce(new Promise((resolve) => { resolveList = resolve }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveApprove = resolve }))
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.memoryProposals = [memoryProposal('p1', 2)]
    const list = store.loadMemoryProposals()
    const approval = store.approveMemoryProposal('p1', 2)
    resolveApprove?.({ ok: true, json: async () => memoryProposal('p1', 3) })
    await approval
    resolveList?.({ ok: true, json: async () => ({ proposals: [memoryProposal('p1', 2)] }) })
    await list
    expect(store.memoryProposals).toEqual([])
  })

  it('computes context percent from chars', () => {
    const store = useCodingStore()
    store.contextSnapshot = {
      model_id: 'model-a',
      configured: true,
      used_tokens: 0,
      model_limit_tokens: 100000,
      effective_limit_tokens: 60000,
      output_reserve_tokens: 40000,
      usage_ratio: 0,
      level: 'normal',
      estimated: false,
      compactable: true,
      active_run_id: null,
      context_operation_active: false,
      checkpoint_id: '',
      resume_status: 'canonical_fallback',
      checkpoint_resume_enabled: true,
      latest_attempt: null,
      stale_started: false,
    }
    store.contextChars = 30000
    expect(store.contextPercent).toBe(50)
  })

  it('clamps context percent at 100', () => {
    const store = useCodingStore()
    store.contextSnapshot = {
      model_id: 'model-a',
      configured: true,
      used_tokens: 0,
      model_limit_tokens: 100000,
      effective_limit_tokens: 60000,
      output_reserve_tokens: 40000,
      usage_ratio: 0,
      level: 'normal',
      estimated: false,
      compactable: true,
      active_run_id: null,
      context_operation_active: false,
      checkpoint_id: '',
      resume_status: 'canonical_fallback',
      checkpoint_resume_enabled: true,
      latest_attempt: null,
      stale_started: false,
    }
    store.contextChars = 99999
    expect(store.contextPercent).toBe(100)
  })

  it('appends tool activity on tool_call event', () => {
    const store = useCodingStore()
    store.messages = [{ role: 'assistant', content: '', tools: [], isThinking: true }]
    store.handleServerEvent({
      type: 'tool_call',
      tool: 'read_file',
      args: { path: 'README.md' },
    } as never)
    expect(store.messages[0].tools).toHaveLength(1)
    expect(store.messages[0].tools![0].tool).toBe('read_file')
    expect(store.messages[0].tools![0].status).toBe('running')
  })

  it('updates tool activity on tool_result event', () => {
    const store = useCodingStore()
    store.messages = [
      {
        role: 'assistant',
        content: '',
        tools: [{ tool: 'read_file', args: {}, status: 'running', content: '' }],
        isThinking: true,
      },
    ]
    store.handleServerEvent({
      type: 'tool_result',
      tool: 'read_file',
      args: {},
      content: '# Sage',
      is_error: false,
    } as never)
    expect(store.messages[0].tools![0].status).toBe('done')
    expect(store.messages[0].tools![0].content).toBe('# Sage')
  })

  it('stores pending approval from approval_required event', () => {
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.handleServerEvent({
      type: 'approval_required',
      approval_id: 'appr_1',
      tool: 'write_file',
      args: { path: 'README.md' },
      description: 'write_file requires approval.',
      pattern_key: 'tool:write_file',
    } as never)

    expect(store.pendingApproval?.approval_id).toBe('appr_1')
    expect(store.pendingApproval?.tool).toBe('write_file')
    store.disconnect()
  })

  it('builds write approval diff preview from current file content', async () => {
    const fetchMock = vi.fn().mockImplementation((url: URL) => {
      if (url.pathname.endsWith('/files')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ path: '.', entries: [{ name: 'README.md', is_dir: false }] }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ path: 'README.md', content: 'old title\n', lines: 1 }),
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'

    store.handleServerEvent({
      type: 'approval_required',
      approval_id: 'appr_1',
      tool: 'write_file',
      args: { path: 'README.md', content: 'new title\n' },
      description: 'write_file requires approval.',
      pattern_key: 'tool:write_file',
    } as never)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(store.pendingApproval?.diff_preview).toEqual([
      { type: 'remove', text: 'old title' },
      { type: 'add', text: 'new title' },
    ])
    store.disconnect()
  })

  it('does not request a nonexistent file while previewing a new write approval', async () => {
    const fetchMock = vi.fn().mockImplementation((url: URL) => {
      if (url.pathname.endsWith('/files')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ path: '.', entries: [] }),
        })
      }
      return Promise.resolve({ ok: false, status: 400 })
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'

    store.handleServerEvent({
      type: 'approval_required',
      approval_id: 'appr_1',
      tool: 'write_file',
      args: { path: 'new-note.txt', content: 'first\n\nthird\n' },
      description: 'write_file requires approval.',
      pattern_key: 'tool:write_file',
    } as never)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(fetchMock.mock.calls.map(([url]) => (url as URL).pathname)).toEqual([
      '/api/v1/coding/c1/files',
    ])
    expect(store.pendingApproval?.diff_preview).toEqual([
      { type: 'add', text: 'first' },
      { type: 'add', text: '' },
      { type: 'add', text: 'third' },
    ])
    store.disconnect()
  })

  it('responds to pending approval and clears it', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.pendingApproval = {
      approval_id: 'appr_1',
      session_id: 'c1',
      tool: 'write_file',
      args: {},
      description: 'write_file requires approval.',
      pattern_key: 'tool:write_file',
    }

    await store.respondApproval('session')

    expect(fetchMock).toHaveBeenCalled()
    expect(store.pendingApproval).toBeNull()
  })

  it('keeps the approval available when submitting the decision fails', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 500 })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.pendingApproval = {
      approval_id: 'appr_1',
      session_id: 'c1',
      tool: 'write_file',
      args: { path: 'README.md' },
      description: 'write_file requires approval.',
      pattern_key: 'tool:write_file',
    }

    await store.respondApproval('once')

    expect(store.pendingApproval?.approval_id).toBe('appr_1')
    expect(store.errorMessage).toContain('respond approval failed: 500')
  })

  it('caches file tree directories', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ path: '.', entries: [{ name: 'src', is_dir: true }] }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'

    await store.loadFiles('.')
    await store.loadFiles('.')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(store.fileTreeEntries).toEqual([{ name: 'src', is_dir: true }])
    expect(store.expandedDirs.has('.')).toBe(true)
  })

  it('filters noise files out of the file tree', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        path: '.',
        entries: [
          { name: 'src', is_dir: true },
          { name: 'README.md', is_dir: false },
          { name: '.env', is_dir: false },
          { name: '.env.local', is_dir: false },
          { name: '.DS_Store', is_dir: false },
          { name: '__pycache__', is_dir: true },
          { name: 'app.pyc', is_dir: false },
          { name: 'node_modules', is_dir: true },
          { name: '.git', is_dir: true },
          { name: 'debug.log', is_dir: false },
        ],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'

    await store.loadFiles('.')

    expect(store.fileTreeEntries).toEqual([
      { name: 'src', is_dir: true },
      { name: 'README.md', is_dir: false },
    ])
  })

  it('refreshes workspace view after successful write tools', async () => {
    const fetchMock = vi.fn().mockImplementation((url: URL) => {
      if (url.pathname.endsWith('/git/status')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            is_git: true,
            branch: 'main',
            dirty_count: 1,
            changed_files: ['note.txt'],
          }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ path: '.', entries: [{ name: 'note.txt', is_dir: false }] }),
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.messages = [
      {
        role: 'assistant',
        content: '',
        tools: [{ tool: 'write_file', args: {}, status: 'running', content: '' }],
        isThinking: true,
      },
    ]

    store.handleServerEvent({
      type: 'tool_result',
      tool: 'write_file',
      args: {},
      content: 'wrote note.txt',
      is_error: false,
    } as never)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(store.gitStatus.dirty_count).toBe(1)
    expect(store.fileTreeEntries).toEqual([{ name: 'note.txt', is_dir: false }])
  })

  it('finalizes message on final event', () => {
    const store = useCodingStore()
    store.messages = [{ role: 'assistant', content: '', tools: [], isThinking: true }]
    store.isThinking = true
    store.handleServerEvent({ type: 'final', content: '完成' } as never)
    expect(store.messages[0].content).toBe('完成')
    expect(store.messages[0].isThinking).toBe(false)
    expect(store.isThinking).toBe(false)
  })

  it('switches permission mode through the REST API', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, mode: 'accept_edits' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'

    await expect(store.changePermissionMode('accept_edits')).resolves.toBe(true)

    expect(store.permissionMode).toBe('accept_edits')
    const url = fetchMock.mock.calls[0][0] as URL
    expect(url.pathname).toBe('/api/v1/coding/c1/permission-mode')
  })

  it('requests manual context compaction and applies the returned snapshot', async () => {
    const snapshot = {
      model_id: 'model-a',
      configured: true,
      used_tokens: 12000,
      model_limit_tokens: 100000,
      effective_limit_tokens: 80000,
      output_reserve_tokens: 20000,
      usage_ratio: 0.15,
      level: 'normal',
      estimated: false,
      compactable: true,
      active_run_id: null,
      context_operation_active: false,
      checkpoint_id: 'compact-1',
      resume_status: 'checkpoint_active',
      checkpoint_resume_enabled: true,
      latest_attempt: null,
      stale_started: false,
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        compaction_id: 'compact-1',
        applied: true,
        before_tokens: 50000,
        after_tokens: 12000,
        archived_items: 9,
        retryable: false,
        reason: '',
        context: snapshot,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.contextSnapshot = { ...snapshot, used_tokens: 50000, checkpoint_id: '' }

    await expect(store.compactContext()).resolves.toBe(true)

    expect(store.contextChars).toBe(12000)
    expect(store.compactionState).toBe('succeeded')
    const url = fetchMock.mock.calls[0][0] as URL
    expect(url.pathname).toBe('/api/v1/coding/c1/context/compact')
  })

  it('applies delayed compaction to its source session after switching away', async () => {
    const response = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(response.promise))
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.contextSnapshot = {
      model_id: 'model-a', configured: true, used_tokens: 500, model_limit_tokens: 1_000,
      effective_limit_tokens: 900, output_reserve_tokens: 100, usage_ratio: 0.55,
      level: 'compact', estimated: false, compactable: true, active_run_id: null,
      context_operation_active: false, checkpoint_id: '', resume_status: '',
      checkpoint_resume_enabled: true, latest_attempt: null, stale_started: false,
    }
    const compacting = store.compactContext()
    store.sessionId = 'session-b'
    response.resolve({ ok: true, json: async () => ({
      applied: true, before_tokens: 500, after_tokens: 100, archived_items: 2,
      reason: '', retryable: false, compaction_id: 'compact-a',
      context: { ...store.sessionsById['session-a'].contextSnapshot, used_tokens: 100 },
    }) })
    await compacting

    expect(store.sessionsById['session-a'].compactionState).toBe('succeeded')
    expect(store.sessionsById['session-a'].contextChars).toBe(100)
    expect(store.compactionState).toBe('idle')
  })

  it('applies delayed model and permission changes to their source session', async () => {
    const modelResponse = deferred<{ ok: boolean }>()
    const permissionResponse = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const fetchMock = vi.fn()
      .mockReturnValueOnce(modelResponse.promise)
      .mockReturnValueOnce(permissionResponse.promise)
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'session-a'
    const changingModel = store.changeModel('model-a')
    const changingPermission = store.changePermissionMode('accept_edits')
    store.sessionId = 'session-b'
    modelResponse.resolve({ ok: true })
    permissionResponse.resolve({ ok: true, json: async () => ({ ok: true, mode: 'accept_edits' }) })
    await Promise.all([changingModel, changingPermission])

    expect(store.sessionsById['session-a'].currentModelId).toBe('model-a')
    expect(store.sessionsById['session-a'].permissionMode).toBe('accept_edits')
    expect(store.permissionMode).toBe('default')
  })

  it('ignores a context response from a previously selected session', async () => {
    let resolveResponse: ((value: unknown) => void) | undefined
    vi.stubGlobal(
      'fetch',
      vi.fn().mockReturnValue(
        new Promise((resolve) => {
          resolveResponse = resolve
        }),
      ),
    )
    const store = useCodingStore()
    store.sessionId = 'c1'
    const pending = store.loadContext()
    store.sessionId = 'c2'
    resolveResponse?.({
      ok: true,
      json: async () => ({ configured: true, used_tokens: 999 }),
    })

    await pending

    expect(store.contextSnapshot).toBeNull()
    expect(store.contextChars).toBe(0)
  })

  it('keeps an active run observable when the websocket errors', () => {
    const sockets: Array<FakeSocket> = []
    class FakeSocket {
      readyState = 1
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null
      send = vi.fn()
      close = vi.fn()
      constructor(_url: string) {
        sockets.push(this)
      }
    }
    vi.stubGlobal('WebSocket', FakeSocket)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.isThinking = true
    store.activeRun = { run_id: 'run-1', status: 'running' }
    store.thinkingPhase = '正在请求模型...'

    store.connectSocket()
    sockets[0].onerror?.()

    expect(store.isThinking).toBe(true)
    expect(store.activeRun?.run_id).toBe('run-1')
    expect(store.errorMessage).toBe('连接中断')
    store.disconnect()
  })

  it('shows the preparation phase as soon as a message is accepted by the stream', () => {
    const sockets: FakeSocket[] = []
    class FakeSocket {
      readyState = 1
      onopen: (() => void) | null = null
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null
      send = vi.fn()
      close = vi.fn()
      constructor() {
        sockets.push(this)
      }
    }
    vi.stubGlobal('WebSocket', FakeSocket)
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.connectSocket()
    sockets[0].onopen?.()

    store.sendMessage('检查这个模块')

    expect(store.isThinking).toBe(true)
    expect(store.thinkingPhase).toBe('正在理解任务')
    expect(store.optimisticMessage?.content).toBe('检查这个模块')
    expect(store.messages[0]).toMatchObject({ role: 'user', content: '检查这个模块' })
    store.disconnect()
  })

  it('keeps the optimistic user message until the matching timeline user event arrives', () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.optimisticMessage = {
      id: 'optimistic:session-a:1', role: 'user', content: '检查这个模块',
    }
    store.messages = [store.optimisticMessage]

    store.handleTimelineEvent('session-a', timelineEvent(
      'session-a', 1, 'system', { event: 'run_started' }, 'run-new',
    ))
    expect(store.optimisticMessage?.content).toBe('检查这个模块')
    expect(store.messages.some((message) => message.content === '检查这个模块')).toBe(true)

    store.handleTimelineEvent('session-a', timelineEvent(
      'session-a', 2, 'user', { type: 'user', content: '检查这个模块' }, 'run-new',
    ))
    expect(store.optimisticMessage).toBeNull()
    expect(store.messages.filter((message) => message.content === '检查这个模块')).toHaveLength(1)
  })

  it('does not clear the current optimistic message while loading identical older history', () => {
    const store = useCodingStore()
    store.sessionId = 'session-a'
    store.optimisticMessage = {
      id: 'optimistic:session-a:2', role: 'user', content: '重复问题',
    }

    store.mergeTimelinePage('session-a', [timelineEvent(
      'session-a', 1, 'user', { type: 'user', content: '重复问题' }, 'old-run',
    )], { next_cursor: 5, older_cursor: null }, 'older')

    expect(store.optimisticMessage?.content).toBe('重复问题')
  })

  it('handles cancelled event as a stopped assistant message', () => {
    const store = useCodingStore()
    store.messages = [{ role: 'assistant', content: '', tools: [], isThinking: true }]
    store.isThinking = true

    store.handleServerEvent({ type: 'cancelled', content: '已停止当前运行。' } as never)

    expect(store.messages[0].content).toBe('已停止当前运行。')
    expect(store.messages[0].isThinking).toBe(false)
    expect(store.isThinking).toBe(false)
  })

  it('requests stop for current run', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.isThinking = true
    store.activeRun = { run_id: 'run-current', status: 'running' }

    await store.stopCurrentRun()

    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL), {
      credentials: 'include',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: 'run-current' }),
    })
  })

  it('does not send an unbound stop request without an active run id', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.isThinking = true

    await store.stopCurrentRun()

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('loads run history and run detail', async () => {
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
          events: [{ type: 'final', content: 'done' }],
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
    const store = useCodingStore()
    store.sessionId = 'c1'

    await store.loadRuns()
    await store.loadRunDetail('run_1')

    expect(store.runs[0].run_id).toBe('run_1')
    expect(store.selectedRun?.events[0].content).toBe('done')
    expect(store.selectedRun?.timeline[0].title).toBe('Final answer')
  })

  it('loads coding session history', async () => {
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
            runtime_profile: 'legacy',
            message_count: 2,
          },
        ],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()

    await store.loadSessions()

    expect(store.codingSessions[0].title).toBe('读 README')
  })

  it('keeps existing session history and reports a failed session-list request', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }))
    const store = useCodingStore()
    store.codingSessions = [{
      session_id: 'existing', title: '保留会话', workspace_root: '/tmp', created_at: '', updated_at: '', runtime_mode: 'default', runtime_profile: 'legacy', message_count: 1,
    }]

    await expect(store.loadSessions()).rejects.toThrow('fetch sessions failed')

    expect(store.codingSessions.map((session) => session.session_id)).toEqual(['existing'])
  })

  it('selects and resumes a coding session', async () => {
    const socketInstances: Array<{ close: ReturnType<typeof vi.fn> }> = []
    class FakeSocket {
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      close = vi.fn()

      constructor(_url: string) {
        socketInstances.push(this)
      }
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ session_id: 's2', workspace_root: '/tmp/repo' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [
            timelineEvent('s2', 1, 'user', { type: 'user', content: '读 README' }),
            timelineEvent('s2', 2, 'assistant', { type: 'final', content: 'README 里是 Sage。' }),
          ],
          next_cursor: 2,
          has_more: false,
          older_cursor: null,
          latest_cursor: 2,
          active_run: null,
        }),
      })
      .mockResolvedValue({
        ok: true,
        json: async () => ({ runs: [] }),
      })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeSocket)
    const store = useCodingStore()
    store.sessionId = 's1'
    store.messages = [{ role: 'assistant', content: 'old' }]

    await store.selectSession('s2')

    expect(store.sessionId).toBe('s2')
    expect(store.workspaceRoot).toBe('/tmp/repo')
    expect(store.messages.map(({ role, content }) => ({ role, content }))).toEqual([
      { role: 'user', content: '读 README' },
      { role: 'assistant', content: 'README 里是 Sage。' },
    ])
    expect(socketInstances).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL), {
      credentials: 'include', method: 'POST',
    })
  })

  it('keeps the active session stream connected when resuming another session fails', async () => {
    const sockets: Array<{ close: ReturnType<typeof vi.fn> }> = []
    class FakeSocket {
      readyState = 1
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null
      close = vi.fn()
      constructor(_url: string) { sockets.push(this) }
    }
    vi.stubGlobal('WebSocket', FakeSocket)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404 }))
    const store = useCodingStore()
    store.sessionId = 's-old'
    store.activeRun = { run_id: 'run-old', status: 'running' }
    store.connectSocket()

    await expect(store.selectSession('missing')).rejects.toThrow('resume session failed')

    expect(store.sessionId).toBe('s-old')
    expect(store.activeRun?.run_id).toBe('run-old')
    expect(sockets).toHaveLength(1)
    expect(sockets[0].close).not.toHaveBeenCalled()
    store.disconnect()
  })

  it('keeps the active session stream connected when creating a session fails', async () => {
    const sockets: Array<{ close: ReturnType<typeof vi.fn> }> = []
    class FakeSocket {
      readyState = 1
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null
      close = vi.fn()
      constructor(_url: string) { sockets.push(this) }
    }
    vi.stubGlobal('WebSocket', FakeSocket)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }))
    const store = useCodingStore()
    store.sessionId = 's-old'
    store.activeRun = { run_id: 'run-old', status: 'running' }
    store.connectSocket()

    await expect(store.startNewSession()).rejects.toThrow('Coding session request failed')

    expect(store.sessionId).toBe('s-old')
    expect(store.activeRun?.run_id).toBe('run-old')
    expect(sockets).toHaveLength(1)
    expect(sockets[0].close).not.toHaveBeenCalled()
    store.disconnect()
  })

  it('replays and reconnects the current session after its stream is disconnected', async () => {
    const sockets: Array<{ close: ReturnType<typeof vi.fn> }> = []
    class FakeSocket {
      readyState = 1
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null
      close = vi.fn()
      constructor(_url: string) { sockets.push(this) }
    }
    vi.stubGlobal('WebSocket', FakeSocket)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ items: [], next_cursor: 0, has_more: false, older_cursor: null, latest_cursor: 0, active_run: null, entries: [], runs: [], sessions: [], configured: false, used_tokens: 0 }),
    }))
    const store = useCodingStore()
    store.sessionId = 's-current'
    store.connectSocket()
    store.disconnect()

    await store.restoreCurrentSession()

    expect(store.sessionId).toBe('s-current')
    expect(sockets).toHaveLength(2)
    expect(sockets[0].close).toHaveBeenCalled()
    store.disconnect()
  })

  it('does not reconnect after a disconnected view finishes restoring its session', async () => {
    const timeline = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const sockets: unknown[] = []
    class FakeSocket {
      readyState = 1
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null
      close = vi.fn()
      constructor(_url: string) { sockets.push(this) }
    }
    vi.stubGlobal('WebSocket', FakeSocket)
    vi.stubGlobal('fetch', vi.fn((input: URL | string) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      if (url.pathname.endsWith('/timeline')) return timeline.promise
      if (url.pathname.endsWith('/messages')) return Promise.resolve({ ok: true, json: async () => ({ messages: [] }) })
      if (url.pathname.endsWith('/files')) return Promise.resolve({ ok: true, json: async () => ({ entries: [] }) })
      if (url.pathname.endsWith('/git/status')) return Promise.resolve({ ok: true, json: async () => ({ is_git: false, branch: '', dirty_count: 0, changed_files: [] }) })
      if (url.pathname.endsWith('/context')) return Promise.resolve({ ok: true, json: async () => ({ configured: false, used_tokens: 0 }) })
      return Promise.resolve({ ok: true, json: async () => ({ sessions: [], runs: [] }) })
    }))
    const store = useCodingStore()
    store.sessionId = 's-current'

    const restoring = store.restoreCurrentSession()
    store.disconnect()
    timeline.resolve({
      ok: true,
      json: async () => ({ items: [], next_cursor: 0, has_more: false, older_cursor: null, latest_cursor: 0, active_run: null }),
    })
    await restoring

    expect(sockets).toHaveLength(0)
  })

  it('does not connect an initialized session after the owning view disconnects', async () => {
    const session = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const sockets: unknown[] = []
    class FakeSocket {
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      close = vi.fn()
      constructor(_url: string) { sockets.push(this) }
    }
    vi.stubGlobal('WebSocket', FakeSocket)
    vi.stubGlobal('fetch', vi.fn((input: URL | string, init?: RequestInit) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      if (init?.method === 'POST' && url.pathname.endsWith('/coding/session')) return session.promise
      return Promise.resolve({ ok: true, json: async () => ({ items: [], next_cursor: 0, has_more: false, older_cursor: null, latest_cursor: 0, active_run: null, sessions: [], runs: [], entries: [], configured: false, used_tokens: 0 }) })
    }))
    const store = useCodingStore()

    const initializing = store.initialize()
    store.disconnect()
    session.resolve({ ok: true, json: async () => ({ session_id: 'abandoned', workspace_root: '/tmp', permission_mode: 'default' }) })
    await initializing

    expect(store.sessionId).toBe('')
    expect(sockets).toHaveLength(0)
  })

  it('uses latest-wins semantics for concurrent session selection', async () => {
    class FakeSocket {
      readyState = 1
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null
      send = vi.fn()
      close = vi.fn()
    }
    vi.stubGlobal('WebSocket', FakeSocket)
    const resumeA = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const resumeB = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: URL | string, init?: RequestInit) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      if (init?.method === 'POST' && url.pathname.includes('session-a/resume')) return resumeA.promise
      if (init?.method === 'POST' && url.pathname.includes('session-b/resume')) return resumeB.promise
      if (url.pathname.endsWith('/timeline')) return Promise.resolve({ ok: true, json: async () => ({
        items: [], next_cursor: 0, has_more: false, older_cursor: null, latest_cursor: 0, active_run: null,
      }) })
      if (url.pathname.endsWith('/messages')) return Promise.resolve({ ok: true, json: async () => ({ messages: [] }) })
      if (url.pathname.endsWith('/files')) return Promise.resolve({ ok: true, json: async () => ({ entries: [] }) })
      if (url.pathname.endsWith('/git/status')) return Promise.resolve({ ok: true, json: async () => ({ is_git: false, branch: '', dirty_count: 0, changed_files: [] }) })
      if (url.pathname.endsWith('/context')) return Promise.resolve({ ok: true, json: async () => ({ configured: false, used_tokens: 0 }) })
      return Promise.resolve({ ok: true, json: async () => ({ sessions: [], runs: [], proposals: [] }) })
    }))
    const store = useCodingStore()
    const selectingA = store.selectSession('session-a')
    const selectingB = store.selectSession('session-b')
    resumeB.resolve({ ok: true, json: async () => ({
      session_id: 'session-b', workspace_root: '/b', permission_mode: 'default',
    }) })
    await selectingB
    resumeA.resolve({ ok: true, json: async () => ({
      session_id: 'session-a', workspace_root: '/a', permission_mode: 'default',
    }) })
    await selectingA

    expect(store.sessionId).toBe('session-b')
    expect(store.workspaceRoot).toBe('/b')
    store.disconnect()
  })

  it('ignores stale runs, git and files responses from session A', async () => {
    const runsA = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const gitA = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const filesA = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const fetchMock = vi.fn()
      .mockReturnValueOnce(runsA.promise)
      .mockReturnValueOnce(gitA.promise)
      .mockReturnValueOnce(filesA.promise)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [{ run_id: 'run-b' }] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ is_git: true, branch: 'b', dirty_count: 0, changed_files: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ entries: [{ name: 'b.ts', is_dir: false }] }) })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'session-a'
    const oldRuns = store.loadRuns()
    const oldGit = store.loadGitStatus()
    const oldFiles = store.loadFiles('.', true)
    store.sessionId = 'session-b'
    await Promise.all([store.loadRuns(), store.loadGitStatus(), store.loadFiles('.', true)])
    runsA.resolve({ ok: true, json: async () => ({ runs: [{ run_id: 'run-a' }] }) })
    gitA.resolve({ ok: true, json: async () => ({ is_git: true, branch: 'a', dirty_count: 0, changed_files: [] }) })
    filesA.resolve({ ok: true, json: async () => ({ entries: [{ name: 'a.ts', is_dir: false }] }) })
    await Promise.all([oldRuns, oldGit, oldFiles])

    expect(store.runs[0].run_id).toBe('run-b')
    expect(store.gitStatus.branch).toBe('b')
    expect(store.fileTreeEntries).toEqual([{ name: 'b.ts', is_dir: false }])
  })

  it('starts a new coding session from the workbench', async () => {
    const socketInstances: Array<{ close: ReturnType<typeof vi.fn> }> = []
    class FakeSocket {
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      close = vi.fn()

      constructor(_url: string) {
        socketInstances.push(this)
      }
    }
    const fetchMock = vi.fn((input: URL | string, init?: RequestInit) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      if (init?.method === 'POST' && url.pathname.endsWith('/coding/session')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 's-new', workspace_root: '/tmp/repo', runtime_profile: 'deerflow_v2',
          }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({
          runs: [], sessions: [], models: [], current: null, reasoning_mode: 'off',
          runtime_profiles: ['legacy', 'deerflow_v2'], default_runtime_profile: 'deerflow_v2',
        }),
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeSocket)
    const store = useCodingStore()
    store.sessionId = 's-old'
    store.workspaceRoot = '/tmp/repo'
    store.messages = [{ role: 'assistant', content: 'old answer' }]
    store.runs = [
      {
        run_id: 'run_old',
        status: 'completed',
        event_count: 1,
        tool_count: 0,
        error_count: 0,
        last_event_type: 'final',
        started_at: '',
        updated_at: '',
      },
    ]
    store.selectedRun = { run_id: 'run_old', events: [], timeline: [] }

    await store.startNewSession()

    expect(store.sessionId).toBe('s-new')
    expect(store.workspaceRoot).toBe('/tmp/repo')
    expect(store.messages).toEqual([])
    expect(store.runs).toEqual([])
    expect(store.selectedRun).toBeNull()
    expect(socketInstances).toHaveLength(1)
  })

  it('sends an assistant-home prompt once after the new session stream opens', async () => {
    const sockets: FakeSocket[] = []
    class FakeSocket {
      readyState = 0
      onopen: (() => void) | null = null
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null
      send = vi.fn()
      close = vi.fn()

      constructor(_url: string) {
        sockets.push(this)
      }

      open() {
        this.readyState = 1
        this.onopen?.()
      }
    }
    const empty = {
      items: [], next_cursor: 0, has_more: false, older_cursor: null,
      latest_cursor: 0, active_run: null, messages: [], models: [], current: null,
      entries: [], path: '.', is_git: false, branch: '', dirty_count: 0,
      changed_files: [], sessions: [], runs: [], proposals: [], configured: false,
      used_tokens: 0,
    }
    vi.stubGlobal('fetch', vi.fn((input: URL | string, init?: RequestInit) => {
      const url = input instanceof URL ? input : new URL(input, window.location.origin)
      if (init?.method === 'POST' && url.pathname.endsWith('/coding/session')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 'home-session', workspace_root: '/tmp/repo',
            permission_mode: 'default',
          }),
        })
      }
      return Promise.resolve({ ok: true, json: async () => empty })
    }))
    vi.stubGlobal('WebSocket', FakeSocket)
    const store = useCodingStore()

    const sessionId = await store.startSessionWithPrompt('  帮我梳理这个项目  ')

    expect(sessionId).toBe('home-session')
    expect(sockets).toHaveLength(1)
    expect(sockets[0].send).not.toHaveBeenCalled()
    sockets[0].open()
    sockets[0].open()
    expect(sockets[0].send).toHaveBeenCalledTimes(1)
    expect(sockets[0].send).toHaveBeenCalledWith(JSON.stringify({ content: '帮我梳理这个项目' }))
    expect(store.optimisticMessage?.content).toBe('帮我梳理这个项目')
    store.disconnect()
  })

  it('refreshes run history when a run finishes', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ runs: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.messages = [{ role: 'assistant', content: '', tools: [], isThinking: true }]
    store.isThinking = true

    store.handleServerEvent({ type: 'final', content: '完成' } as never)
    store.handleServerEvent({
      type: 'run_finished',
      status: 'completed',
      duration_ms: 1234,
      tool_steps: 5,
    } as never)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL), { credentials: 'include' })
  })

  it('does not let a stale terminal hide a newer active run', () => {
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.activeRun = { run_id: 'run-new', status: 'running' }
    store.isThinking = true
    store.thinkingPhase = '正在运行'

    store.handleTimelineEvent('c1', timelineEvent(
      'c1', 1, 'terminal', { type: 'run_finished', status: 'cancelled' }, 'run-old',
    ))

    expect(store.activeRun?.run_id).toBe('run-new')
    expect(store.isThinking).toBe(true)
    expect(store.thinkingPhase).toBe('正在运行')
  })

  it('stores plan review from plan_ready_for_review event', () => {
    const store = useCodingStore()
    store.handleServerEvent({
      type: 'plan_ready_for_review',
      run_id: 'run_xxx',
      review_id: 'plan_review_1',
      plan_path: '.coding/plans/xxx-plan.md',
      summary: '# Plan',
    } as never)

    expect(store.planReview?.review_id).toBe('plan_review_1')
    expect(store.planReview?.plan_path).toBe('.coding/plans/xxx-plan.md')
    expect(store.planReview?.summary).toBe('# Plan')
  })

  it('approves a plan via REST and clears state from response', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'approved', mode: 'default' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.runtimeMode = 'plan'
    store.planTopic = 'refactor'
    store.planPath = '.coding/plans/xxx-plan.md'
    store.planReview = {
      review_id: 'plan_review_1',
      plan_path: '.coding/plans/xxx-plan.md',
      summary: '# Plan',
    }

    await store.approvePlan()

    const calledUrl = fetchMock.mock.calls[0][0] as URL
    expect(calledUrl.pathname).toBe('/api/v1/coding/c1/plan/approve')
    // approvePlan applies the REST response locally since runtime_mode_changed
    // is not pushed over WebSocket (the REST call runs outside run_turn).
    expect(store.planReview).toBeNull()
    expect(store.runtimeMode).toBe('default')
    expect(store.planTopic).toBe('')
    expect(store.planPath).toBe('')
  })

  it('rejects a plan via REST and clears plan review locally', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'
    store.planReview = {
      review_id: 'plan_review_1',
      plan_path: '.coding/plans/xxx-plan.md',
      summary: '# Plan',
    }

    await store.rejectPlan()

    const calledUrl = fetchMock.mock.calls[0][0] as URL
    expect(calledUrl.pathname).toBe('/api/v1/coding/c1/plan/reject')
    expect(store.planReview).toBeNull()
  })

  it('does nothing when approving or rejecting without a plan review', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)
    const store = useCodingStore()
    store.sessionId = 'c1'

    await store.approvePlan()
    await store.rejectPlan()

    expect(fetchMock).not.toHaveBeenCalled()
  })
})
