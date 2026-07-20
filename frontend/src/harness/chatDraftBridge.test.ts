import { beforeEach, describe, expect, it, vi } from 'vitest'
import { consumeHarnessChatDraft, stageHarnessChatDraft } from './chatDraftBridge'
import type { HarnessSurfaceContext } from './types'

const context: HarnessSurfaceContext = {
  surface: 'knowledge',
  workspaceId: 'knowledge-local',
  resource: { type: 'knowledge_page', id: 'page-1', revision: 'rev-1' },
  selection: { type: 'graph_node', id: 'node-1', revision: 'rev-1' },
  graphRevision: 'graph-1',
  operationRefs: [],
}

describe('chatDraftBridge', () => {
  beforeEach(() => {
    sessionStorage.clear()
    vi.useRealTimers()
  })

  it('moves a frozen surface context into the shared chat composer once', () => {
    expect(stageHarnessChatDraft('  研究这个节点  ', context)).toBe(true)
    expect(consumeHarnessChatDraft()).toMatchObject({ content: '研究这个节点', context })
    expect(consumeHarnessChatDraft()).toBeNull()
  })

  it('drops expired or malformed drafts', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-19T10:00:00Z'))
    stageHarnessChatDraft('研究节点', context)
    vi.advanceTimersByTime(16 * 60 * 1_000)
    expect(consumeHarnessChatDraft()).toBeNull()

    sessionStorage.setItem('sage.harness.pending-chat-draft.v1', '{broken')
    expect(consumeHarnessChatDraft()).toBeNull()
  })
})
