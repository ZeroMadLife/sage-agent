import type { HarnessProjection, HarnessStageStatus } from './types'

export type LearningPathStep = {
  id: 'goal' | 'knowledge' | 'evidence' | 'practice' | 'mastery' | 'deposit'
  label: string
  caption: string
  status: HarnessStageStatus
  sourceStageId?: string
}

const EVIDENCE_TOOL = /(knowledge|retriev|search|web|browser|mcp)/i

export function projectLearningPath(projection: HarnessProjection): LearningPathStep[] {
  const stage = new Map(projection.stages.map((item) => [item.id, item]))
  const receive = stage.get('receive')
  const context = stage.get('context')
  const plan = stage.get('plan')
  const act = stage.get('act')
  const memory = stage.get('memory')
  const actIsEvidence = Boolean(act?.detail && EVIDENCE_TOOL.test(act.detail))
  const practiceSource = act && !actIsEvidence ? act : plan

  return [
    {
      id: 'goal', label: '目标', caption: receive?.label || '等待目标',
      status: receive?.status || 'pending', sourceStageId: receive?.id,
    },
    {
      id: 'knowledge', label: '已有知识', caption: context?.label || '等待上下文',
      status: context?.status || 'pending', sourceStageId: context?.id,
    },
    {
      id: 'evidence', label: '补充证据',
      caption: actIsEvidence ? act?.detail || act?.label || '检索证据' : '等待检索事实',
      status: actIsEvidence ? act?.status || 'pending' : 'pending',
      sourceStageId: actIsEvidence ? act?.id : undefined,
    },
    {
      id: 'practice', label: '练习',
      caption: practiceSource?.detail || practiceSource?.label || '等待实践任务',
      status: practiceSource?.status || 'pending', sourceStageId: practiceSource?.id,
    },
    {
      id: 'mastery', label: '掌握度', caption: '尚未记录掌握度', status: 'pending',
    },
    {
      id: 'deposit', label: '沉淀', caption: memory?.label || '等待 Wiki / Memory',
      status: memory?.status || 'pending', sourceStageId: memory?.id,
    },
  ]
}
