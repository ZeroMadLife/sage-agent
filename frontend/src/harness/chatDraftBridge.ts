import type { HarnessSurfaceContext } from './types'

const STORAGE_KEY = 'sage.harness.pending-chat-draft.v1'
const DEFAULT_MAX_AGE_MS = 15 * 60 * 1_000

export type HarnessChatDraft = {
  content: string
  context: HarnessSurfaceContext
  createdAt: number
}
function isSurfaceContext(value: unknown): value is HarnessSurfaceContext {
  if (!value || typeof value !== 'object') return false
  const context = value as Partial<HarnessSurfaceContext>
  return (
    ['growth', 'knowledge', 'coding'].includes(context.surface ?? '')
    && typeof context.workspaceId === 'string'
    && Array.isArray(context.operationRefs)
  )
}

export function stageHarnessChatDraft(
  content: string,
  context: HarnessSurfaceContext,
  storage: Storage = sessionStorage,
) {
  const normalized = content.trim()
  if (!normalized) return false
  const draft: HarnessChatDraft = { content: normalized, context, createdAt: Date.now() }
  storage.setItem(STORAGE_KEY, JSON.stringify(draft))
  return true
}

export function consumeHarnessChatDraft(
  storage: Storage = sessionStorage,
  maxAgeMs = DEFAULT_MAX_AGE_MS,
): HarnessChatDraft | null {
  const raw = storage.getItem(STORAGE_KEY)
  storage.removeItem(STORAGE_KEY)
  if (!raw) return null
  try {
    const draft = JSON.parse(raw) as Partial<HarnessChatDraft>
    if (
      typeof draft.content !== 'string'
      || !draft.content.trim()
      || typeof draft.createdAt !== 'number'
      || Date.now() - draft.createdAt > maxAgeMs
      || !isSurfaceContext(draft.context)
    ) return null
    return { content: draft.content.trim(), context: draft.context, createdAt: draft.createdAt }
  } catch {
    return null
  }
}
