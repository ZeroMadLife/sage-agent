import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useCodingStore } from './coding'

describe('coding store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
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

  it('resets thinking state and phase when the websocket errors', () => {
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
    store.thinkingPhase = '正在请求模型...'

    store.connectSocket()
    sockets[0].onerror?.()

    expect(store.isThinking).toBe(false)
    expect(store.thinkingPhase).toBe('')
    expect(store.errorMessage).toBe('连接中断')
    store.disconnect()
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

    await store.stopCurrentRun()

    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL), { method: 'POST' })
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
    expect(store.messages).toEqual([
      { role: 'user', content: '读 README' },
      { role: 'assistant', content: 'README 里是 Sage。' },
    ])
    expect(socketInstances).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL), { method: 'POST' })
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
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ session_id: 's-new', workspace_root: '/tmp/repo' }),
      })
      .mockResolvedValue({
        ok: true,
        json: async () => ({ runs: [], sessions: [] }),
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

    expect(fetchMock).toHaveBeenCalledWith(expect.any(URL))
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
