export type HarnessReviewStatus = 'empty' | 'running' | 'ready' | 'review' | 'complete' | 'failed'

export type HarnessReviewEvidenceItem = {
  id: string
  title: string
  source: string
  pageRevision: string
  sourceRevision?: string
  excerpt: string
}

export type HarnessReviewEvidence = {
  status: HarnessReviewStatus
  query: string
  items: HarnessReviewEvidenceItem[]
  omittedCount: number
}

export type HarnessReviewPractice = {
  status: HarnessReviewStatus
  headline: string
  toolCount: number
  completedToolCount: number
  failedToolCount: number
  approvalCount: number
  durationMs?: number
  changedFiles: string[]
}

export type HarnessReviewDeposit = {
  status: HarnessReviewStatus
  proposalId: string
  revision: number
  items: string[]
  source: string
}

export type HarnessReviewBundle = {
  runId: string
  evidence: HarnessReviewEvidence
  practice: HarnessReviewPractice
  deposit: HarnessReviewDeposit
}

export function emptyHarnessReviewBundle(runId = ''): HarnessReviewBundle {
  return {
    runId,
    evidence: { status: 'empty', query: '', items: [], omittedCount: 0 },
    practice: {
      status: 'empty', headline: '', toolCount: 0, completedToolCount: 0,
      failedToolCount: 0, approvalCount: 0, changedFiles: [],
    },
    deposit: { status: 'empty', proposalId: '', revision: 0, items: [], source: '' },
  }
}
