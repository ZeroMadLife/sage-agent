import type {
  AssistantHomeSummary,
  LearningActivationResponse,
  LearningKickoffDispatchResponse,
  LearningTaskDraftInput,
  LearningTaskPatchInput,
  LearningTaskResponse,
} from '../types/api'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || window.location.origin

export async function fetchAssistantHome(): Promise<AssistantHomeSummary> {
  const response = await fetch(new URL('/api/v1/assistant/home', API_BASE_URL), {
    credentials: 'include',
  })
  if (!response.ok) {
    if (response.status === 401) throw new Error('登录状态已失效，请重新登录')
    throw new Error(`首页摘要加载失败：${response.status}`)
  }
  return (await response.json()) as AssistantHomeSummary
}

class LearningRequestError extends Error {
  readonly status: number
  readonly code: string

  constructor(message: string, status: number, code = '') {
    super(message)
    this.name = 'LearningRequestError'
    this.status = status
    this.code = code
  }
}

export function isLearningRequestStatus(cause: unknown, status: number): boolean {
  return cause instanceof LearningRequestError && cause.status === status
}

async function learningRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(new URL(path, API_BASE_URL), {
    credentials: 'include',
    cache: 'no-store',
    ...init,
  })
  if (!response.ok) {
    let message = ''
    let code = ''
    try {
      const payload = await response.json() as {
        detail?: string | { code?: string; message?: string }
      }
      message = typeof payload.detail === 'string' ? payload.detail : payload.detail?.message || ''
      code = typeof payload.detail === 'string' ? '' : payload.detail?.code || ''
    } catch {
      // The status-based fallback remains useful when a proxy returns a non-JSON error page.
    }
    if (response.status === 401) {
      throw new LearningRequestError('登录状态已失效，请重新登录', response.status, code)
    }
    throw new LearningRequestError(
      message || `学习任务请求失败：${response.status}`,
      response.status,
      code,
    )
  }
  return (await response.json()) as T
}

function jsonRequest(method: 'POST' | 'PATCH', body: unknown, headers: HeadersInit = {}): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
  }
}

export function fetchLearningTasks(): Promise<LearningTaskResponse[]> {
  return learningRequest('/api/v1/learning/tasks')
}

export function fetchLearningTask(taskId: string): Promise<LearningTaskResponse> {
  return learningRequest(`/api/v1/learning/tasks/${encodeURIComponent(taskId)}`)
}

export function createLearningDraft(
  input: LearningTaskDraftInput,
): Promise<LearningTaskResponse> {
  return learningRequest('/api/v1/learning/tasks/draft', jsonRequest('POST', input))
}

export function updateLearningDraft(
  taskId: string,
  input: LearningTaskPatchInput,
): Promise<LearningTaskResponse> {
  return learningRequest(
    `/api/v1/learning/tasks/${encodeURIComponent(taskId)}`,
    jsonRequest('PATCH', input),
  )
}

export function activateLearningTask(
  taskId: string,
  expectedRevision: number,
  idempotencyKey: string,
): Promise<LearningActivationResponse> {
  return learningRequest(
    `/api/v1/learning/tasks/${encodeURIComponent(taskId)}/activate`,
    jsonRequest('POST', { expected_revision: expectedRevision }, {
      'Idempotency-Key': idempotencyKey,
    }),
  )
}

export function fetchLearningActivation(taskId: string): Promise<LearningActivationResponse> {
  return learningRequest(`/api/v1/learning/tasks/${encodeURIComponent(taskId)}/activation`)
}

export function fetchLearningKickoff(taskId: string): Promise<LearningKickoffDispatchResponse> {
  return learningRequest(`/api/v1/learning/tasks/${encodeURIComponent(taskId)}/kickoff`)
}

export function dispatchLearningKickoff(
  taskId: string,
  expectedRevision: number,
  idempotencyKey: string,
): Promise<LearningKickoffDispatchResponse> {
  return learningRequest(
    `/api/v1/learning/tasks/${encodeURIComponent(taskId)}/kickoff`,
    jsonRequest('POST', { expected_revision: expectedRevision }, {
      'Idempotency-Key': idempotencyKey,
    }),
  )
}
