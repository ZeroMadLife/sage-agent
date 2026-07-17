const MAX_CITATIONS = 12
const MAX_EXCERPT_CHARS = 1200

export type KnowledgeRetrievalCitationViewModel = {
  citationId: string
  rank: number
  pageRevision: string
  sourceRevision?: string
  sourceKind?: string
  sourceRelativePath?: string
  title: string
  headingPath: string[]
  blockId?: string
  excerpt: string
  truncated: boolean
}

export type KnowledgeRetrievalViewModel = {
  status: 'evidence_found' | 'no_evidence' | 'unavailable'
  query: string
  usedTokens: number
  tokenBudget: number
  omittedCount: number
  citations: KnowledgeRetrievalCitationViewModel[]
}

export function parseKnowledgeRetrieval(content: string): KnowledgeRetrievalViewModel | null {
  let value: unknown
  try {
    value = JSON.parse(content)
  } catch {
    return null
  }
  if (!isRecord(value) || !isStatus(value.status) || !Array.isArray(value.citations)) return null

  const citations = value.citations
    .slice(0, MAX_CITATIONS)
    .map((item, index) => parseCitation(item, index))
    .filter((item): item is KnowledgeRetrievalCitationViewModel => item !== null)
  if (value.status === 'evidence_found' && citations.length === 0) return null

  return {
    status: value.status,
    query: clippedString(value.query, 400),
    usedTokens: nonNegativeInteger(value.used_tokens),
    tokenBudget: nonNegativeInteger(value.token_budget),
    omittedCount: nonNegativeInteger(value.omitted_count),
    citations,
  }
}

function parseCitation(value: unknown, index: number): KnowledgeRetrievalCitationViewModel | null {
  if (!isRecord(value)) return null
  const citationId = clippedString(value.citation_id, 160)
  const pageRevision = clippedString(value.page_revision, 160)
  const excerptSource = clippedString(value.excerpt, MAX_EXCERPT_CHARS + 1)
  if (!citationId || !pageRevision || !excerptSource) return null
  const excerpt = excerptSource.slice(0, MAX_EXCERPT_CHARS)
  const headingPath = Array.isArray(value.heading_path)
    ? value.heading_path.slice(0, 8).map((item) => clippedString(item, 160)).filter(Boolean)
    : []

  return {
    citationId,
    rank: positiveInteger(value.rank) || index + 1,
    pageRevision,
    sourceRevision: optionalString(value.source_revision, 160),
    sourceKind: optionalString(value.source_kind, 80),
    sourceRelativePath: optionalString(value.source_relative_path, 500),
    title: clippedString(value.title, 240) || citationId,
    headingPath,
    blockId: optionalString(value.block_id, 160),
    excerpt,
    truncated: value.truncated === true || excerptSource.length > MAX_EXCERPT_CHARS,
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isStatus(value: unknown): value is KnowledgeRetrievalViewModel['status'] {
  return value === 'evidence_found' || value === 'no_evidence' || value === 'unavailable'
}

function clippedString(value: unknown, limit: number): string {
  return typeof value === 'string' ? value.trim().slice(0, limit) : ''
}

function optionalString(value: unknown, limit: number): string | undefined {
  return clippedString(value, limit) || undefined
}

function nonNegativeInteger(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0
}

function positiveInteger(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0
}
