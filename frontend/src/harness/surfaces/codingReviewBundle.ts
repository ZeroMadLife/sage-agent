import type { DiffInfo } from '../../stores/codingEvents'
import type { TimelineTurn } from '../../stores/codingTimeline'
import type { CodingRunSummary, MemoryProposal } from '../../types/api'
import {
  emptyHarnessReviewBundle,
  type HarnessReviewBundle,
  type HarnessReviewEvidenceItem,
  type HarnessReviewStatus,
} from '../reviewBundle'

export type CodingReviewBundleInput = {
  runId: string
  turn: Pick<TimelineTurn, 'run_id' | 'tools'> | null
  run: CodingRunSummary | null
  diff: DiffInfo | null
  memoryProposals: readonly MemoryProposal[]
}

export function projectCodingReviewBundle(input: CodingReviewBundleInput): HarnessReviewBundle {
  if (!input.runId) return emptyHarnessReviewBundle()

  const tools = input.turn?.run_id === input.runId ? input.turn.tools : []
  const knowledgeTools = tools.filter((tool) => tool.tool === 'knowledge_search')
  const citations = new Map<string, HarnessReviewEvidenceItem>()
  let query = ''
  let omittedCount = 0
  for (const tool of knowledgeTools) {
    if (!tool.retrieval) continue
    query ||= tool.retrieval.query
    omittedCount += tool.retrieval.omittedCount
    for (const citation of tool.retrieval.citations) {
      if (citations.has(citation.citationId)) continue
      citations.set(citation.citationId, {
        id: citation.citationId,
        title: citation.title,
        source: citation.sourceRelativePath || citation.sourceKind || 'Sage Knowledge',
        pageRevision: citation.pageRevision,
        sourceRevision: citation.sourceRevision,
        excerpt: citation.excerpt,
      })
    }
  }

  const evidenceItems = [...citations.values()]
  const evidenceStatus: HarnessReviewStatus = evidenceItems.length
    ? 'ready'
    : knowledgeTools.some((tool) => tool.status === 'running' || tool.status === 'queued')
      ? 'running'
      : knowledgeTools.some((tool) => tool.status === 'error' || tool.is_error)
        ? 'failed'
        : 'empty'

  const audit = input.run?.audit
  const toolCount = audit?.tool_count ?? input.run?.tool_count ?? tools.length
  const completedToolCount = audit?.completed_tool_count
    ?? tools.filter((tool) => tool.status === 'completed' || tool.status === 'done').length
  const failedToolCount = audit?.failed_tool_count
    ?? tools.filter((tool) => tool.status === 'error' || tool.is_error).length
  const changedFiles = [...new Set([
    ...(audit?.changed_files ?? []),
    ...(input.run?.changed_files ?? []),
    ...(input.diff?.changed_files ?? []),
  ])]
  const practiceStatus = runStatus(input.run?.status, Boolean(input.turn || input.run))
  const headline = audit?.headline || practiceHeadline(practiceStatus, toolCount, changedFiles.length)

  const proposal = input.memoryProposals
    .filter((item) => item.run_id === input.runId && item.status === 'pending')
    .slice()
    .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))[0]

  return {
    runId: input.runId,
    evidence: { status: evidenceStatus, query, items: evidenceItems, omittedCount },
    practice: {
      status: practiceStatus,
      headline,
      toolCount,
      completedToolCount,
      failedToolCount,
      approvalCount: audit?.approval_count ?? input.turn?.tools.filter(
        (tool) => tool.status === 'blocked',
      ).length ?? 0,
      durationMs: audit?.duration_ms,
      changedFiles,
    },
    deposit: proposal ? {
      status: 'review',
      proposalId: proposal.proposal_id,
      revision: proposal.revision,
      items: proposal.candidates.map((candidate) => candidate.content).filter(Boolean),
      source: proposal.candidates[0]?.source || 'reflection',
    } : {
      status: 'empty', proposalId: '', revision: 0, items: [], source: '',
    },
  }
}

function runStatus(status: string | undefined, hasFacts: boolean): HarnessReviewStatus {
  const normalized = status?.toLocaleLowerCase() || ''
  if (['completed', 'complete', 'done', 'succeeded', 'success'].includes(normalized)) return 'complete'
  if (['failed', 'error', 'cancelled', 'interrupted'].includes(normalized)) return 'failed'
  return hasFacts ? 'running' : 'empty'
}

function practiceHeadline(status: HarnessReviewStatus, toolCount: number, changedFileCount: number) {
  if (status === 'failed') return '本轮实践未完成，保留了失败证据'
  if (status === 'running') return '正在收集本轮实践结果'
  if (status === 'complete') {
    if (changedFileCount) return `已完成验证并产生 ${changedFileCount} 个文件变更`
    return toolCount ? `已完成 ${toolCount} 项工具验证` : '本轮实践已完成'
  }
  return ''
}
