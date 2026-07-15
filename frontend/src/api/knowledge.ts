import type {
  KnowledgePage,
  KnowledgeJob,
  KnowledgeJobEvent,
  KnowledgeJobItem,
  KnowledgeMigrationPlan,
  KnowledgeMigrationResult,
  KnowledgeProposal,
  KnowledgeWorkspaceSummary,
} from '../types/api'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || window.location.origin

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(new URL(path, API_BASE_URL), {
    credentials: 'include',
    ...init,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail || `知识库请求失败：${response.status}`)
  }
  return (await response.json()) as T
}

export function fetchKnowledgeSummary(): Promise<KnowledgeWorkspaceSummary> {
  return request('/api/v1/knowledge')
}

export function fetchPendingKnowledgeMigration(): Promise<KnowledgeMigrationPlan> {
  return request('/api/v1/knowledge/migrations/pending')
}

export function applyPendingKnowledgeMigration(
  expectedPlanId: string,
): Promise<KnowledgeMigrationResult> {
  return request('/api/v1/knowledge/migrations/pending/apply', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_plan_id: expectedPlanId }),
  })
}

export function fetchKnowledgeProposals(
  status: 'pending' | 'approved' | 'rejected' | null = 'pending',
): Promise<KnowledgeProposal[]> {
  const url = new URL('/api/v1/knowledge/proposals', API_BASE_URL)
  if (status) url.searchParams.set('status', status)
  return request<{ proposals: KnowledgeProposal[] }>(`${url.pathname}${url.search}`)
    .then((response) => response.proposals)
}

export function undoKnowledgeAutoApply(
  proposalId: string,
  expectedPageRevision: string,
): Promise<KnowledgeProposal> {
  return request(
    `/api/v1/knowledge/proposals/${encodeURIComponent(proposalId)}/undo-auto-apply`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_page_revision: expectedPageRevision }),
    },
  )
}

export function fetchKnowledgePages(): Promise<KnowledgePage[]> {
  return request<{ pages: KnowledgePage[] }>('/api/v1/knowledge/pages')
    .then((response) => response.pages)
}

export function ingestKnowledgeSource(
  sourceRootId: string,
  relativePath: string,
): Promise<KnowledgeProposal> {
  return request('/api/v1/knowledge/ingest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_root_id: sourceRootId, relative_path: relativePath }),
  })
}

export function createKnowledgeJob(
  sourceRootId: string,
  relativeDirectory: string,
): Promise<KnowledgeJob> {
  return request('/api/v1/knowledge/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source_root_id: sourceRootId,
      relative_directory: relativeDirectory || '.',
    }),
  })
}

export function fetchKnowledgeJobs(): Promise<KnowledgeJob[]> {
  return request<{ jobs: KnowledgeJob[] }>('/api/v1/knowledge/jobs')
    .then((response) => response.jobs)
}

export function fetchKnowledgeJob(jobId: string, includeItems = true): Promise<KnowledgeJob> {
  const url = new URL(`/api/v1/knowledge/jobs/${encodeURIComponent(jobId)}`, API_BASE_URL)
  url.searchParams.set('include_items', String(includeItems))
  return request(`${url.pathname}${url.search}`)
}

export function cancelKnowledgeJob(jobId: string): Promise<KnowledgeJob> {
  return request(`/api/v1/knowledge/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
  })
}

export function retryKnowledgeJobItem(jobId: string, itemId: string): Promise<KnowledgeJobItem> {
  return request(
    `/api/v1/knowledge/jobs/${encodeURIComponent(jobId)}/items/${encodeURIComponent(itemId)}/retry`,
    { method: 'POST' },
  )
}

export function buildKnowledgeJobStreamUrl(jobId: string, after = 0): string {
  const base = new URL(API_BASE_URL, window.location.origin)
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
  base.pathname = `/api/v1/knowledge/jobs/${encodeURIComponent(jobId)}/stream`
  base.search = ''
  base.searchParams.set('after', String(after))
  return base.toString()
}

export function parseKnowledgeJobEvent(value: unknown, jobId: string): KnowledgeJobEvent {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('收到无效的知识任务事件')
  }
  const event = value as Record<string, unknown>
  if (
    typeof event.event_id !== 'string' ||
    event.job_id !== jobId ||
    !Number.isSafeInteger(event.sequence) ||
    Number(event.sequence) < 1 ||
    typeof event.kind !== 'string' ||
    typeof event.status !== 'string' ||
    typeof event.created_at !== 'string'
  ) {
    throw new Error('收到无效的知识任务事件')
  }
  return value as KnowledgeJobEvent
}

export function transitionKnowledgeProposal(
  proposalId: string,
  action: 'approve' | 'reject',
  expectedRevision: number,
): Promise<KnowledgeProposal> {
  return request(`/api/v1/knowledge/proposals/${encodeURIComponent(proposalId)}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_revision: expectedRevision }),
  })
}

export function proposeKnowledgeRollback(
  pageId: string,
  targetRevisionId: string,
  expectedPageRevision: string,
): Promise<KnowledgeProposal> {
  return request(`/api/v1/knowledge/pages/${encodeURIComponent(pageId)}/rollback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      target_revision_id: targetRevisionId,
      expected_page_revision: expectedPageRevision,
    }),
  })
}
