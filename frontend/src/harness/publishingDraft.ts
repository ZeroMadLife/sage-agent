export type PublishingDraft = {
  title: string
  summary: string
  slug: string
  category: 'article' | 'note' | 'milestone'
  body: string
  updatedAt: string
}
const STORAGE_KEY = 'sage.growth.publishing-draft.v1'

export const defaultPublishingDraft: PublishingDraft = {
  title: '如何让 Agent Harness 支持可靠恢复',
  summary: '从状态快照、事件时间线到幂等执行，记录一次把“能跑”变成“可恢复、可验证”的工程实践。',
  slug: 'agent-harness-recovery',
  category: 'article',
  body: `## 为什么“恢复”不是重新运行

一个长期运行的 Agent，不仅要知道任务执行到了哪里，还要知道哪些副作用已经发生。恢复的目标不是把整个流程再跑一遍，而是从最后一个已确认状态继续。

> 可靠恢复 = 稳定状态快照 + 可回放事件 + 幂等副作用。

## 从 Checkpoint 到 Timeline

Checkpoint 保存可继续执行的状态，Timeline 保留用户可见、可审计的事实。二者结合后，系统才能区分“已经完成”“正在等待”和“尚未开始”。`,
  updatedAt: new Date().toISOString(),
}

function isDraft(value: unknown): value is PublishingDraft {
  if (!value || typeof value !== 'object') return false
  const draft = value as Partial<PublishingDraft>
  return (
    typeof draft.title === 'string'
    && typeof draft.summary === 'string'
    && typeof draft.slug === 'string'
    && ['article', 'note', 'milestone'].includes(draft.category ?? '')
    && typeof draft.body === 'string'
    && typeof draft.updatedAt === 'string'
  )
}

export function loadPublishingDraft(storage: Storage = localStorage): PublishingDraft {
  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (!raw) return { ...defaultPublishingDraft }
    const parsed: unknown = JSON.parse(raw)
    return isDraft(parsed) ? parsed : { ...defaultPublishingDraft }
  } catch {
    return { ...defaultPublishingDraft }
  }
}

export function savePublishingDraft(draft: PublishingDraft, storage: Storage = localStorage) {
  const next = { ...draft, updatedAt: new Date().toISOString() }
  storage.setItem(STORAGE_KEY, JSON.stringify(next))
  return next
}

export function draftWordCount(body: string) {
  const words = body.trim().match(/[\p{Script=Han}]|[\p{L}\p{N}_-]+/gu)
  return words?.length ?? 0
}
