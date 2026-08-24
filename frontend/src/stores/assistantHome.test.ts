import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { useAssistantHomeStore } from './assistantHome'

function learningTask(status: 'draft' | 'active' | 'activation_failed' = 'draft') {
  return {
    version: 1,
    workspace_id: 'workspace-1',
    task_id: 'ltask_1',
    task_revision: 2,
    template_id: 'general_learning',
    topic: '学习 checkpoint 与 resume',
    desired_outcome: '能够解释恢复边界',
    learner_profile: {
      starting_level: 'beginner' as const,
      time_budget_minutes_per_week: 180,
      target_date: null,
    },
    source_policy: {
      knowledge: 'preferred' as const,
      web: 'forbidden' as const,
      domains: [],
      freshness: 'all' as const,
    },
    risk_class: 'general_education' as const,
    risk_notice: null,
    clarification: { required_fields: [], questions: [], ready_to_activate: true },
    learning_plan_id: null,
    learning_plan_hash: null,
    dag_hash: null,
    learning_goal_ref: status === 'active' ? { goal_id: 'goal-1', goal_revision: 'g1' } : null,
    status,
    created_at: '2026-08-24T00:00:00Z',
    updated_at: '2026-08-24T00:00:00Z',
  }
}

function activationReceipt() {
  return {
    version: 3,
    workspace_id: 'workspace-1',
    task_id: 'ltask_1',
    task_revision: 2,
    session_id: 'learning-session',
    thread_goal_revision: 1,
    learning_goal_ref: { goal_id: 'goal-1', goal_revision: 'g1' },
    learning_plan_id: null,
    learning_plan_hash: null,
    turn_context_plan_id: 'turnplan-1',
    turn_context_plan_hash: 'sha256:plan',
    dag_hash: null,
    plan_id: 'turnplan-1',
    plan_hash: 'sha256:plan',
    catalog_revision: 'catalog-1',
    capability_revision: 'capability-1',
    allowed_capabilities: ['local:knowledge_search'],
    source_policy_snapshot: learningTask().source_policy,
    source_policy_revision: 'source-1',
    resume_validation_version: 'canonical_l0_v3' as const,
    receipt_status: 'active' as const,
    failure_code: null,
    created_at: '2026-08-24T00:00:00Z',
    updated_at: '2026-08-24T00:00:01Z',
    completed_at: '2026-08-24T00:00:01Z',
  }
}

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

it('restores an active learning task from server state after refresh', async () => {
  const task = learningTask('active')
  const receipt = activationReceipt()
  vi.stubGlobal('fetch', vi.fn((input: URL | string) => {
    const url = input instanceof URL ? input : new URL(input, window.location.origin)
    if (url.pathname.endsWith('/activation')) {
      return Promise.resolve({ ok: true, json: async () => receipt })
    }
    return Promise.resolve({ ok: true, json: async () => [task] })
  }))
  const store = useAssistantHomeStore()

  await store.restoreLearningTask()

  expect(store.learningState).toBe('active')
  expect(store.learningTask?.task_id).toBe('ltask_1')
  expect(store.activationReceipt?.session_id).toBe('learning-session')
  vi.unstubAllGlobals()
})

it('deduplicates confirmation and returns one server-issued active receipt', async () => {
  const receipt = activationReceipt()
  let resolveActivation!: (value: unknown) => void
  const pending = new Promise((resolve) => { resolveActivation = resolve })
  const fetchMock = vi.fn((input: URL | string, init?: RequestInit) => {
    const url = input instanceof URL ? input : new URL(input, window.location.origin)
    if (url.pathname.endsWith('/activate') && init?.method === 'POST') return pending
    return Promise.resolve({ ok: true, json: async () => learningTask() })
  })
  vi.stubGlobal('fetch', fetchMock)
  const store = useAssistantHomeStore()
  store.learningTask = learningTask()
  store.learningState = 'needs_confirmation'

  const first = store.activateCurrentLearningTask()
  const second = store.activateCurrentLearningTask()
  expect(fetchMock.mock.calls.filter(([input]) => String(input).includes('/activate'))).toHaveLength(1)
  expect(store.learningState).toBe('activating')

  resolveActivation({ ok: true, json: async () => receipt })
  await expect(first).resolves.toEqual(receipt)
  await expect(second).resolves.toEqual(receipt)
  expect(store.learningState).toBe('active')
  vi.unstubAllGlobals()
})

it('keeps a failed task editable and retries with the stable revision key', async () => {
  const failedTask = learningTask('activation_failed')
  const receipt = activationReceipt()
  let attempts = 0
  const fetchMock = vi.fn((input: URL | string, init?: RequestInit) => {
    const url = input instanceof URL ? input : new URL(input, window.location.origin)
    if (url.pathname.endsWith('/activate') && init?.method === 'POST') {
      attempts += 1
      if (attempts === 1) {
        return Promise.resolve({
          ok: false, status: 503,
          json: async () => ({ detail: { message: 'bootstrap 暂时失败' } }),
        })
      }
      return Promise.resolve({ ok: true, json: async () => receipt })
    }
    if (url.pathname.endsWith('/activation')) {
      return Promise.resolve({
        ok: true,
        json: async () => ({ ...receipt, receipt_status: 'activation_failed', failure_code: 'boom' }),
      })
    }
    return Promise.resolve({ ok: true, json: async () => failedTask })
  })
  vi.stubGlobal('fetch', fetchMock)
  const store = useAssistantHomeStore()
  store.learningTask = learningTask()
  store.learningState = 'needs_confirmation'

  await expect(store.activateCurrentLearningTask()).rejects.toThrow('bootstrap 暂时失败')
  expect(store.learningState).toBe('activation_failed')
  expect(store.learningTask?.topic).toBe('学习 checkpoint 与 resume')

  await expect(store.activateCurrentLearningTask()).resolves.toEqual(receipt)
  const activationCalls = fetchMock.mock.calls.filter(([input]) => String(input).includes('/activate'))
  expect(activationCalls).toHaveLength(2)
  expect((activationCalls[0][1] as RequestInit).headers).toEqual(
    expect.objectContaining({ 'Idempotency-Key': 'learning-ltask_1-r2' }),
  )
  expect((activationCalls[1][1] as RequestInit).headers).toEqual(
    expect.objectContaining({ 'Idempotency-Key': 'learning-ltask_1-r2' }),
  )
  vi.unstubAllGlobals()
})

it('recovers the active receipt when the activation response is lost', async () => {
  const activeTask = learningTask('active')
  const receipt = activationReceipt()
  const fetchMock = vi.fn((input: URL | string, init?: RequestInit) => {
    const url = input instanceof URL ? input : new URL(input, window.location.origin)
    if (url.pathname.endsWith('/activate') && init?.method === 'POST') {
      return Promise.resolve({
        ok: false, status: 503,
        json: async () => ({ detail: { message: '响应连接已断开' } }),
      })
    }
    if (url.pathname.endsWith('/activation')) {
      return Promise.resolve({ ok: true, json: async () => receipt })
    }
    return Promise.resolve({ ok: true, json: async () => activeTask })
  })
  vi.stubGlobal('fetch', fetchMock)
  const store = useAssistantHomeStore()
  store.learningTask = learningTask()
  store.learningState = 'needs_confirmation'

  await expect(store.activateCurrentLearningTask()).resolves.toEqual(receipt)
  expect(store.learningState).toBe('active')
  expect(store.activationReceipt?.session_id).toBe('learning-session')
  vi.unstubAllGlobals()
})
