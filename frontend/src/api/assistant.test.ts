import { afterEach, expect, it, vi } from 'vitest'
import {
  activateLearningTask,
  createLearningDraft,
  dispatchLearningKickoff,
  fetchAssistantHome,
  fetchLearningActivation,
  fetchLearningKickoff,
  fetchLearningTasks,
  updateLearningDraft,
} from './assistant'

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

it('uses the browser-safe learning task control-plane contract', async () => {
  const task = { task_id: 'ltask_1', task_revision: 2 }
  const receipt = { task_id: 'ltask_1', task_revision: 2, receipt_status: 'active' }
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => [task] })
    .mockResolvedValueOnce({ ok: true, json: async () => task })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ ...task, task_revision: 3 }) })
    .mockResolvedValueOnce({ ok: true, json: async () => receipt })
    .mockResolvedValueOnce({ ok: true, json: async () => receipt })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ ...receipt, receipt_status: 'accepted' }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ ...receipt, receipt_status: 'accepted' }) })
  vi.stubGlobal('fetch', fetchMock)

  await expect(fetchLearningTasks()).resolves.toEqual([task])
  await expect(createLearningDraft({ topic: '学习 checkpoint' })).resolves.toEqual(task)
  await expect(updateLearningDraft('ltask_1', {
    expected_revision: 2,
    desired_outcome: '能够解释恢复边界',
  })).resolves.toMatchObject({ task_revision: 3 })
  await expect(activateLearningTask('ltask_1', 2, 'learning-ltask_1-r2')).resolves.toEqual(receipt)
  await expect(fetchLearningActivation('ltask_1')).resolves.toEqual(receipt)
  await expect(fetchLearningKickoff('ltask_1')).resolves.toMatchObject({ receipt_status: 'accepted' })
  await expect(dispatchLearningKickoff('ltask_1', 2, 'learning-kickoff-ltask_1-r2'))
    .resolves.toMatchObject({ receipt_status: 'accepted' })

  expect(fetchMock).toHaveBeenNthCalledWith(1, expect.any(URL), {
    credentials: 'include', cache: 'no-store',
  })
  expect(fetchMock).toHaveBeenNthCalledWith(2, expect.any(URL), expect.objectContaining({
    method: 'POST', credentials: 'include',
    body: JSON.stringify({ topic: '学习 checkpoint' }),
  }))
  expect(fetchMock).toHaveBeenNthCalledWith(3, expect.any(URL), expect.objectContaining({
    method: 'PATCH', credentials: 'include',
  }))
  expect(fetchMock).toHaveBeenNthCalledWith(4, expect.any(URL), expect.objectContaining({
    method: 'POST', credentials: 'include',
    headers: expect.objectContaining({ 'Idempotency-Key': 'learning-ltask_1-r2' }),
    body: JSON.stringify({ expected_revision: 2 }),
  }))
  expect(fetchMock).toHaveBeenNthCalledWith(7, expect.any(URL), expect.objectContaining({
    method: 'POST', credentials: 'include',
    headers: expect.objectContaining({ 'Idempotency-Key': 'learning-kickoff-ltask_1-r2' }),
    body: JSON.stringify({ expected_revision: 2 }),
  }))
})

it('surfaces the server activation failure message', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: false,
    status: 503,
    json: async () => ({ detail: { code: 'learning_activation_failed', message: '会话初始化失败' } }),
  }))

  await expect(activateLearningTask('ltask_1', 1, 'learning-ltask_1-r1'))
    .rejects.toThrow('会话初始化失败')
})
