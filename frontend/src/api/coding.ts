import type {
  CodingApprovalChoice,
  CodingApprovalResponse,
  CodingCompactResponse,
  CodingContextSnapshot,
  CodingFileContentResponse,
  CodingFilesResponse,
  CodingGitStatusResponse,
  CodingMcpServersResponse,
  CodingModelsResponse,
  CodingProviderSettings,
  CodingProviderSettingsUpdate,
  CodingUsageSummary,
  CloudModelDefaultResponse,
  CloudModelDiscoveryResponse,
  CloudModelProvider,
  CloudModelProviderCreate,
  CloudModelProvidersResponse,
  CloudModelProviderTestResponse,
  CloudModelProviderUpdate,
  MemoryProposal,
  MemoryProposalsResponse,
  CodingRunDetailResponse,
  CodingRunDiff,
  CodingRunsResponse,
  CodingRuntimeProfile,
  CodingSessionMessagesResponse,
  CodingSessionResponse,
  CodingSessionSummary,
  CodingSessionsResponse,
  CodingTimelineResponse,
  CodingSkillDetailResponse,
  CodingSkillsResponse,
  PermissionMode,
} from '../types/api'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || window.location.origin
const TIMELINE_KINDS = new Set(['user', 'assistant', 'model', 'tool', 'approval', 'context', 'memory', 'agent', 'terminal', 'system', 'run', 'harness'])
const TIMELINE_STATUSES = new Set(['pending', 'queued', 'running', 'blocked', 'done', 'completed', 'cancelled', 'error', 'interrupted', 'retryable'])

function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  return fetch(input, { credentials: 'include', ...init })
}

function parseTimelineResponse(value: unknown, sessionId: string): CodingTimelineResponse {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('收到无效的时间线响应')
  const response = value as Record<string, unknown>
  if (!Array.isArray(response.items) || !Number.isSafeInteger(response.next_cursor) || Number(response.next_cursor) < 0 ||
    typeof response.has_more !== 'boolean' ||
    !(response.older_cursor === null || Number.isSafeInteger(response.older_cursor)) ||
    !Number.isSafeInteger(response.latest_cursor) || Number(response.latest_cursor) < 0 ||
    (response.older_cursor !== null && Number(response.older_cursor) < 0) ||
    Number(response.next_cursor) > Number(response.latest_cursor) ||
    (response.older_cursor !== null && Number(response.older_cursor) > Number(response.latest_cursor)) ||
    !(response.active_run === null || isActiveRun(response.active_run))) {
    throw new Error('收到无效的时间线响应')
  }
  let previous = 0
  for (const candidate of response.items) {
    if (!isTimelineEvent(candidate, sessionId) || candidate.sequence <= previous) {
      throw new Error('收到无效的时间线响应')
    }
    previous = candidate.sequence
  }
  if (previous > Number(response.latest_cursor)) throw new Error('收到无效的时间线响应')
  return value as CodingTimelineResponse
}

function isActiveRun(value: unknown): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const run = value as Record<string, unknown>
  return typeof run.run_id === 'string' && run.run_id.length > 0 && run.status === 'running'
}

function isTimelineEvent(value: unknown, sessionId: string): value is CodingTimelineResponse['items'][number] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const event = value as Record<string, unknown>
  return typeof event.event_id === 'string' && event.event_id.length > 0 &&
    event.session_id === sessionId && typeof event.run_id === 'string' && event.run_id.length > 0 &&
    Number.isSafeInteger(event.sequence) && Number(event.sequence) > 0 &&
    typeof event.kind === 'string' && TIMELINE_KINDS.has(event.kind) &&
    typeof event.status === 'string' && TIMELINE_STATUSES.has(event.status) &&
    typeof event.timestamp === 'string' && event.timestamp.length > 0 &&
    Boolean(event.payload) && typeof event.payload === 'object' && !Array.isArray(event.payload)
}

export async function startCodingSession(
  workspaceRoot?: string,
  approvalPolicy: 'auto' | 'ask' | 'never' = 'ask',
  runtimeProfile?: CodingRuntimeProfile,
): Promise<CodingSessionResponse> {
  const response = await apiFetch(new URL('/api/v1/coding/session', API_BASE_URL), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      workspace_root: workspaceRoot || null,
      approval_policy: approvalPolicy,
      runtime_profile: runtimeProfile ?? null,
    }),
  })

  if (!response.ok) {
    throw new Error(`Coding session request failed with status ${response.status}`)
  }

  return (await response.json()) as CodingSessionResponse
}

export function buildCodingStreamUrl(sessionId: string, after = 0): string {
  const base = new URL(API_BASE_URL, window.location.origin)
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
  base.pathname = `/api/v1/coding/${sessionId}/stream`
  base.search = ''
  base.searchParams.set('after', String(after))
  return base.toString()
}

export async function fetchCodingTimeline(
  sessionId: string,
  after = 0,
  limit = 100,
): Promise<CodingTimelineResponse> {
  const url = new URL(`/api/v1/coding/session/${sessionId}/timeline`, API_BASE_URL)
  url.searchParams.set('after', String(after))
  url.searchParams.set('limit', String(limit))
  const response = await apiFetch(url)
  if (!response.ok) throw new Error(`fetch timeline failed: ${response.status}`)
  return parseTimelineResponse(await response.json(), sessionId)
}

export async function fetchCodingTimelineTail(
  sessionId: string,
  limit = 100,
): Promise<CodingTimelineResponse> {
  const url = new URL(`/api/v1/coding/session/${sessionId}/timeline`, API_BASE_URL)
  url.searchParams.set('tail', 'true')
  url.searchParams.set('limit', String(limit))
  const response = await apiFetch(url)
  if (!response.ok) throw new Error(`fetch timeline tail failed: ${response.status}`)
  return parseTimelineResponse(await response.json(), sessionId)
}

export async function fetchOlderCodingTimeline(
  sessionId: string,
  before: number,
  limit = 100,
): Promise<CodingTimelineResponse> {
  const url = new URL(`/api/v1/coding/session/${sessionId}/timeline`, API_BASE_URL)
  url.searchParams.set('before', String(before))
  url.searchParams.set('limit', String(limit))
  const response = await apiFetch(url)
  if (!response.ok) throw new Error(`fetch older timeline failed: ${response.status}`)
  return parseTimelineResponse(await response.json(), sessionId)
}

export async function fetchCodingSessions(): Promise<CodingSessionsResponse> {
  const url = new URL('/api/v1/coding/sessions', API_BASE_URL)
  url.searchParams.set('include_archived', 'true')
  const response = await apiFetch(url)
  if (!response.ok) throw new Error(`fetch sessions failed: ${response.status}`)
  return (await response.json()) as CodingSessionsResponse
}

export async function updateCodingSessionMetadata(
  sessionId: string,
  metadata: { title?: string; pinned?: boolean; archived?: boolean },
): Promise<CodingSessionSummary> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/session/${sessionId}/metadata`, API_BASE_URL),
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(metadata),
    },
  )
  if (!response.ok) throw new Error(`update session metadata failed: ${response.status}`)
  return (await response.json()) as CodingSessionSummary
}

export async function resumeCodingSession(sessionId: string): Promise<CodingSessionResponse> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/session/${sessionId}/resume`, API_BASE_URL),
    { method: 'POST' },
  )
  if (!response.ok) throw new Error(`resume session failed: ${response.status}`)
  return (await response.json()) as CodingSessionResponse
}

export async function fetchCodingSessionMessages(
  sessionId: string,
): Promise<CodingSessionMessagesResponse> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/session/${sessionId}/messages`, API_BASE_URL),
  )
  if (!response.ok) throw new Error(`fetch session messages failed: ${response.status}`)
  return (await response.json()) as CodingSessionMessagesResponse
}

export async function fetchCodingFiles(
  sessionId: string,
  path = '.',
): Promise<CodingFilesResponse> {
  const url = new URL(`/api/v1/coding/${sessionId}/files`, API_BASE_URL)
  url.searchParams.set('path', path)
  const response = await apiFetch(url)
  if (!response.ok) throw new Error(`fetch files failed: ${response.status}`)
  return (await response.json()) as CodingFilesResponse
}

export async function fetchCodingFile(
  sessionId: string,
  path: string,
): Promise<CodingFileContentResponse> {
  const url = new URL(`/api/v1/coding/${sessionId}/file`, API_BASE_URL)
  url.searchParams.set('path', path)
  const response = await apiFetch(url)
  if (!response.ok) throw new Error(`fetch file failed: ${response.status}`)
  return (await response.json()) as CodingFileContentResponse
}

export async function fetchCodingGitStatus(
  sessionId: string,
): Promise<CodingGitStatusResponse> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/git/status`, API_BASE_URL),
  )
  if (!response.ok) throw new Error(`fetch git status failed: ${response.status}`)
  return (await response.json()) as CodingGitStatusResponse
}

export async function fetchCodingModels(sessionId?: string): Promise<CodingModelsResponse> {
  const url = new URL('/api/v1/coding/models', API_BASE_URL)
  if (sessionId) url.searchParams.set('session_id', sessionId)
  const response = await apiFetch(url)
  if (!response.ok) throw new Error(`fetch models failed: ${response.status}`)
  return (await response.json()) as CodingModelsResponse
}

export async function switchCodingReasoning(
  sessionId: string,
  mode: 'off' | 'low' | 'medium' | 'high',
): Promise<{ ok: boolean; model_id: string; reasoning_mode: 'off' | 'low' | 'medium' | 'high' }> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/reasoning`, API_BASE_URL),
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    },
  )
  if (!response.ok) throw new Error(`switch reasoning failed: ${response.status}`)
  return (await response.json()) as { ok: boolean; model_id: string; reasoning_mode: 'off' | 'low' | 'medium' | 'high' }
}

export async function fetchCodingProviderSettings(): Promise<CodingProviderSettings> {
  const response = await apiFetch(new URL('/api/v1/coding/providers', API_BASE_URL))
  if (!response.ok) throw new Error(`fetch provider settings failed: ${response.status}`)
  return (await response.json()) as CodingProviderSettings
}

export async function updateCodingProviderSettings(
  settings: CodingProviderSettingsUpdate,
): Promise<CodingProviderSettings> {
  const response = await apiFetch(new URL('/api/v1/coding/providers', API_BASE_URL), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  })
  if (!response.ok) throw new Error(`update provider settings failed: ${response.status}`)
  return (await response.json()) as CodingProviderSettings
}

async function cloudProviderError(response: Response, fallback: string): Promise<Error> {
  try {
    const payload = await response.json() as { detail?: unknown }
    if (typeof payload.detail === 'string' && payload.detail.trim()) {
      return new Error(payload.detail)
    }
  } catch {
    // The status-derived fallback stays stable when an upstream body is absent.
  }
  return new Error(`${fallback}: ${response.status}`)
}

const cloudProviderRequest = {
  credentials: 'include' as const,
  cache: 'no-store' as const,
}

export async function fetchCloudModelProviders(): Promise<CloudModelProvidersResponse | null> {
  const response = await apiFetch(new URL('/api/v1/cloud/model-providers', API_BASE_URL), {
    ...cloudProviderRequest,
  })
  if (response.status === 401) return null
  if (!response.ok) throw await cloudProviderError(response, 'fetch account Providers failed')
  return (await response.json()) as CloudModelProvidersResponse
}

export async function createCloudModelProvider(
  payload: CloudModelProviderCreate,
): Promise<CloudModelProvider> {
  const response = await apiFetch(new URL('/api/v1/cloud/model-providers', API_BASE_URL), {
    ...cloudProviderRequest,
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw await cloudProviderError(response, 'create account Provider failed')
  return (await response.json()) as CloudModelProvider
}

export async function updateCloudModelProvider(
  providerId: string,
  payload: CloudModelProviderUpdate,
): Promise<CloudModelProvider> {
  const response = await apiFetch(
    new URL(`/api/v1/cloud/model-providers/${encodeURIComponent(providerId)}`, API_BASE_URL),
    {
      ...cloudProviderRequest,
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  )
  if (!response.ok) throw await cloudProviderError(response, 'update account Provider failed')
  return (await response.json()) as CloudModelProvider
}

export async function deleteCloudModelProvider(providerId: string): Promise<void> {
  const response = await apiFetch(
    new URL(`/api/v1/cloud/model-providers/${encodeURIComponent(providerId)}`, API_BASE_URL),
    { ...cloudProviderRequest, method: 'DELETE' },
  )
  if (!response.ok) throw await cloudProviderError(response, 'delete account Provider failed')
}

export async function testCloudModelProvider(
  providerId: string,
): Promise<CloudModelProviderTestResponse> {
  const response = await apiFetch(
    new URL(`/api/v1/cloud/model-providers/${encodeURIComponent(providerId)}/test`, API_BASE_URL),
    { ...cloudProviderRequest, method: 'POST' },
  )
  if (!response.ok) throw await cloudProviderError(response, 'test account Provider failed')
  return (await response.json()) as CloudModelProviderTestResponse
}

export async function discoverCloudModelProvider(
  providerId: string,
): Promise<CloudModelDiscoveryResponse> {
  const response = await apiFetch(
    new URL(`/api/v1/cloud/model-providers/${encodeURIComponent(providerId)}/discover-models`, API_BASE_URL),
    { ...cloudProviderRequest, method: 'POST' },
  )
  if (!response.ok) throw await cloudProviderError(response, 'discover account models failed')
  return (await response.json()) as CloudModelDiscoveryResponse
}

export async function setCloudModelDefault(
  providerId: string,
  modelId: string,
): Promise<CloudModelDefaultResponse> {
  const response = await apiFetch(new URL('/api/v1/cloud/model-default', API_BASE_URL), {
    ...cloudProviderRequest,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider_id: providerId, model_id: modelId }),
  })
  if (!response.ok) throw await cloudProviderError(response, 'set account default model failed')
  return (await response.json()) as CloudModelDefaultResponse
}

export async function fetchCodingUsage(
  range: '7d' | '30d' | '90d' | '365d' = '30d',
): Promise<CodingUsageSummary> {
  const url = new URL('/api/v1/coding/usage', API_BASE_URL)
  url.searchParams.set('range', range)
  const response = await apiFetch(url)
  if (!response.ok) throw new Error(`fetch usage failed: ${response.status}`)
  return (await response.json()) as CodingUsageSummary
}

export async function fetchCodingContext(sessionId: string): Promise<CodingContextSnapshot> {
  const response = await apiFetch(new URL(`/api/v1/coding/${sessionId}/context`, API_BASE_URL))
  if (!response.ok) throw new Error(`fetch context failed: ${response.status}`)
  return (await response.json()) as CodingContextSnapshot
}

export async function requestCodingCompaction(
  sessionId: string,
  focus = '',
): Promise<CodingCompactResponse> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/context/compact`, API_BASE_URL),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ focus }),
    },
  )
  if (!response.ok) {
    if (response.status === 409) throw new Error('当前运行或压缩任务正在进行中')
    if (response.status === 422) throw new Error('当前模型未配置上下文窗口')
    throw new Error(`compact context failed: ${response.status}`)
  }
  return (await response.json()) as CodingCompactResponse
}

export async function switchCodingModel(
  sessionId: string,
  modelId: string,
): Promise<{ ok: boolean; model_id: string; reasoning_mode: 'off' | 'low' | 'medium' | 'high' }> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/model`, API_BASE_URL),
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model_id: modelId }),
    },
  )
  if (!response.ok) throw new Error(`switch model failed: ${response.status}`)
  if (typeof response.json !== 'function') {
    return { ok: true, model_id: modelId, reasoning_mode: 'off' }
  }
  return (await response.json()) as { ok: boolean; model_id: string; reasoning_mode: 'off' | 'low' | 'medium' | 'high' }
}

export async function switchPermissionMode(
  sessionId: string,
  mode: PermissionMode,
): Promise<{ ok: boolean; mode: PermissionMode }> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/permission-mode`, API_BASE_URL),
    { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) },
  )
  if (!response.ok) throw new Error(`switch permission mode failed: ${response.status}`)
  return (await response.json()) as { ok: boolean; mode: PermissionMode }
}

export async function fetchCodingSkills(): Promise<CodingSkillsResponse> {
  const response = await apiFetch(new URL('/api/v1/coding/skills', API_BASE_URL))
  if (!response.ok) throw new Error(`fetch skills failed: ${response.status}`)
  return (await response.json()) as CodingSkillsResponse
}

export async function fetchCodingSkill(
  name: string,
): Promise<CodingSkillDetailResponse> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/skills/${name}`, API_BASE_URL),
  )
  if (!response.ok) throw new Error(`fetch skill failed: ${response.status}`)
  return (await response.json()) as CodingSkillDetailResponse
}

export async function fetchCodingMcpServers(): Promise<CodingMcpServersResponse> {
  const response = await apiFetch(new URL('/api/v1/coding/mcp/servers', API_BASE_URL))
  if (!response.ok) throw new Error(`fetch mcp servers failed: ${response.status}`)
  return (await response.json()) as CodingMcpServersResponse
}

export async function fetchCodingApprovalPending(
  sessionId: string,
): Promise<CodingApprovalResponse> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/approval/pending`, API_BASE_URL),
  )
  if (!response.ok) throw new Error(`fetch approval failed: ${response.status}`)
  return (await response.json()) as CodingApprovalResponse
}

export async function respondCodingApproval(
  sessionId: string,
  approvalId: string,
  choice: CodingApprovalChoice,
): Promise<void> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/approval/respond`, API_BASE_URL),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id: approvalId, choice }),
    },
  )
  if (!response.ok) throw new Error(`respond approval failed: ${response.status}`)
}

export async function stopCodingRun(sessionId: string, runId: string): Promise<void> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/run/stop`, API_BASE_URL),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: runId }),
    },
  )
  if (!response.ok) throw new Error(`stop run failed: ${response.status}`)
}

export async function approveCodingPlan(
  sessionId: string,
): Promise<{ status: string; mode: string }> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/plan/approve`, API_BASE_URL),
    { method: 'POST' },
  )
  if (!response.ok) throw new Error(`approve plan failed: ${response.status}`)
  return (await response.json()) as { status: string; mode: string }
}

export async function rejectCodingPlan(sessionId: string): Promise<void> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/plan/reject`, API_BASE_URL),
    { method: 'POST' },
  )
  if (!response.ok) throw new Error(`reject plan failed: ${response.status}`)
}

function memoryProposalError(status: number): Error {
  if (status === 409) return new Error('记忆候选已发生变化，请刷新后重试')
  if (status === 404) return new Error('记忆候选不存在或已处理')
  if (status === 422) return new Error('记忆候选版本无效')
  return new Error('记忆服务暂时不可用，请稍后重试')
}

export async function fetchMemoryProposals(
  sessionId: string,
  status: 'pending' | 'approved' | 'rejected' = 'pending',
): Promise<MemoryProposalsResponse> {
  const url = new URL(`/api/v1/coding/${sessionId}/memory/proposals`, API_BASE_URL)
  url.searchParams.set('status', status)
  const response = await apiFetch(url)
  if (!response.ok) throw memoryProposalError(response.status)
  return (await response.json()) as MemoryProposalsResponse
}

async function transitionMemoryProposal(
  sessionId: string,
  proposalId: string,
  expectedRevision: number,
  action: 'approve' | 'reject',
): Promise<MemoryProposal> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/memory/proposals/${encodeURIComponent(proposalId)}/${action}`, API_BASE_URL),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_revision: expectedRevision }),
    },
  )
  if (!response.ok) throw memoryProposalError(response.status)
  return (await response.json()) as MemoryProposal
}

export function approveMemoryProposal(sessionId: string, proposalId: string, expectedRevision: number) {
  return transitionMemoryProposal(sessionId, proposalId, expectedRevision, 'approve')
}

export function rejectMemoryProposal(sessionId: string, proposalId: string, expectedRevision: number) {
  return transitionMemoryProposal(sessionId, proposalId, expectedRevision, 'reject')
}

export async function fetchCodingRuns(sessionId: string): Promise<CodingRunsResponse> {
  const response = await apiFetch(new URL(`/api/v1/coding/${sessionId}/runs`, API_BASE_URL))
  if (!response.ok) throw new Error(`fetch runs failed: ${response.status}`)
  return (await response.json()) as CodingRunsResponse
}

export async function fetchCodingRun(
  sessionId: string,
  runId: string,
): Promise<CodingRunDetailResponse> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/runs/${runId}`, API_BASE_URL),
  )
  if (!response.ok) throw new Error(`fetch run failed: ${response.status}`)
  return (await response.json()) as CodingRunDetailResponse
}

export async function fetchCodingRunDiff(
  sessionId: string,
  runId: string,
): Promise<CodingRunDiff> {
  const response = await apiFetch(
    new URL(`/api/v1/coding/${sessionId}/runs/${runId}/diff`, API_BASE_URL),
  )
  if (!response.ok) throw new Error(`fetch run diff failed: ${response.status}`)
  return (await response.json()) as CodingRunDiff
}
