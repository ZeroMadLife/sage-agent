import type {
  CodingApprovalChoice,
  CodingApprovalResponse,
  CodingFileContentResponse,
  CodingFilesResponse,
  CodingGitStatusResponse,
  CodingMcpServersResponse,
  CodingModelsResponse,
  CodingRunDetailResponse,
  CodingRunsResponse,
  CodingSessionMessagesResponse,
  CodingSessionResponse,
  CodingSessionsResponse,
  CodingSkillDetailResponse,
  CodingSkillsResponse,
  PermissionMode,
} from '../types/api'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || window.location.origin

export async function startCodingSession(
  workspaceRoot?: string,
  approvalPolicy: 'auto' | 'ask' | 'never' = 'ask',
): Promise<CodingSessionResponse> {
  const response = await fetch(new URL('/api/v1/coding/session', API_BASE_URL), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace_root: workspaceRoot || null, approval_policy: approvalPolicy }),
  })

  if (!response.ok) {
    throw new Error(`Coding session request failed with status ${response.status}`)
  }

  return (await response.json()) as CodingSessionResponse
}

export function buildCodingStreamUrl(sessionId: string): string {
  const base = new URL(API_BASE_URL, window.location.origin)
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
  base.pathname = `/api/v1/coding/${sessionId}/stream`
  base.search = ''
  return base.toString()
}

export async function fetchCodingSessions(): Promise<CodingSessionsResponse> {
  const response = await fetch(new URL('/api/v1/coding/sessions', API_BASE_URL))
  if (!response.ok) throw new Error(`fetch sessions failed: ${response.status}`)
  return (await response.json()) as CodingSessionsResponse
}

export async function resumeCodingSession(sessionId: string): Promise<CodingSessionResponse> {
  const response = await fetch(
    new URL(`/api/v1/coding/session/${sessionId}/resume`, API_BASE_URL),
    { method: 'POST' },
  )
  if (!response.ok) throw new Error(`resume session failed: ${response.status}`)
  return (await response.json()) as CodingSessionResponse
}

export async function fetchCodingSessionMessages(
  sessionId: string,
): Promise<CodingSessionMessagesResponse> {
  const response = await fetch(
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
  const response = await fetch(url)
  if (!response.ok) throw new Error(`fetch files failed: ${response.status}`)
  return (await response.json()) as CodingFilesResponse
}

export async function fetchCodingFile(
  sessionId: string,
  path: string,
): Promise<CodingFileContentResponse> {
  const url = new URL(`/api/v1/coding/${sessionId}/file`, API_BASE_URL)
  url.searchParams.set('path', path)
  const response = await fetch(url)
  if (!response.ok) throw new Error(`fetch file failed: ${response.status}`)
  return (await response.json()) as CodingFileContentResponse
}

export async function fetchCodingGitStatus(
  sessionId: string,
): Promise<CodingGitStatusResponse> {
  const response = await fetch(
    new URL(`/api/v1/coding/${sessionId}/git/status`, API_BASE_URL),
  )
  if (!response.ok) throw new Error(`fetch git status failed: ${response.status}`)
  return (await response.json()) as CodingGitStatusResponse
}

export async function fetchCodingModels(): Promise<CodingModelsResponse> {
  const response = await fetch(new URL('/api/v1/coding/models', API_BASE_URL))
  if (!response.ok) throw new Error(`fetch models failed: ${response.status}`)
  return (await response.json()) as CodingModelsResponse
}

export async function switchCodingModel(
  sessionId: string,
  modelId: string,
): Promise<void> {
  const response = await fetch(
    new URL(`/api/v1/coding/${sessionId}/model`, API_BASE_URL),
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model_id: modelId }),
    },
  )
  if (!response.ok) throw new Error(`switch model failed: ${response.status}`)
}

export async function switchPermissionMode(
  sessionId: string,
  mode: PermissionMode,
): Promise<{ ok: boolean; mode: PermissionMode }> {
  const response = await fetch(
    new URL(`/api/v1/coding/${sessionId}/permission-mode`, API_BASE_URL),
    { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) },
  )
  if (!response.ok) throw new Error(`switch permission mode failed: ${response.status}`)
  return (await response.json()) as { ok: boolean; mode: PermissionMode }
}

export async function fetchCodingSkills(): Promise<CodingSkillsResponse> {
  const response = await fetch(new URL('/api/v1/coding/skills', API_BASE_URL))
  if (!response.ok) throw new Error(`fetch skills failed: ${response.status}`)
  return (await response.json()) as CodingSkillsResponse
}

export async function fetchCodingSkill(
  name: string,
): Promise<CodingSkillDetailResponse> {
  const response = await fetch(
    new URL(`/api/v1/coding/skills/${name}`, API_BASE_URL),
  )
  if (!response.ok) throw new Error(`fetch skill failed: ${response.status}`)
  return (await response.json()) as CodingSkillDetailResponse
}

export async function fetchCodingMcpServers(): Promise<CodingMcpServersResponse> {
  const response = await fetch(new URL('/api/v1/coding/mcp/servers', API_BASE_URL))
  if (!response.ok) throw new Error(`fetch mcp servers failed: ${response.status}`)
  return (await response.json()) as CodingMcpServersResponse
}

export async function fetchCodingApprovalPending(
  sessionId: string,
): Promise<CodingApprovalResponse> {
  const response = await fetch(
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
  const response = await fetch(
    new URL(`/api/v1/coding/${sessionId}/approval/respond`, API_BASE_URL),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id: approvalId, choice }),
    },
  )
  if (!response.ok) throw new Error(`respond approval failed: ${response.status}`)
}

export async function stopCodingRun(sessionId: string): Promise<void> {
  const response = await fetch(
    new URL(`/api/v1/coding/${sessionId}/run/stop`, API_BASE_URL),
    { method: 'POST' },
  )
  if (!response.ok) throw new Error(`stop run failed: ${response.status}`)
}

export async function approveCodingPlan(
  sessionId: string,
): Promise<{ status: string; mode: string }> {
  const response = await fetch(
    new URL(`/api/v1/coding/${sessionId}/plan/approve`, API_BASE_URL),
    { method: 'POST' },
  )
  if (!response.ok) throw new Error(`approve plan failed: ${response.status}`)
  return (await response.json()) as { status: string; mode: string }
}

export async function rejectCodingPlan(sessionId: string): Promise<void> {
  const response = await fetch(
    new URL(`/api/v1/coding/${sessionId}/plan/reject`, API_BASE_URL),
    { method: 'POST' },
  )
  if (!response.ok) throw new Error(`reject plan failed: ${response.status}`)
}

export async function fetchCodingRuns(sessionId: string): Promise<CodingRunsResponse> {
  const response = await fetch(new URL(`/api/v1/coding/${sessionId}/runs`, API_BASE_URL))
  if (!response.ok) throw new Error(`fetch runs failed: ${response.status}`)
  return (await response.json()) as CodingRunsResponse
}

export async function fetchCodingRun(
  sessionId: string,
  runId: string,
): Promise<CodingRunDetailResponse> {
  const response = await fetch(
    new URL(`/api/v1/coding/${sessionId}/runs/${runId}`, API_BASE_URL),
  )
  if (!response.ok) throw new Error(`fetch run failed: ${response.status}`)
  return (await response.json()) as CodingRunDetailResponse
}
