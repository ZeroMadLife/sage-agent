import { describe, expect, it } from 'vitest'
import type { HarnessProjection } from './types'
import { projectLearningPath } from './learningPath'

function projection(actDetail: string): HarnessProjection {
  return {
    definitionId: 'sage.coding.practice', definitionVersion: 1, definitionMissing: false,
    runId: 'run-1', status: 'running', activeStageId: 'act',
    stages: [
      { id: 'receive', label: '接收目标', status: 'completed', visitCount: 1, lastSequence: 1 },
      { id: 'context', label: '组装上下文', status: 'completed', visitCount: 1, lastSequence: 2 },
      { id: 'plan', label: '规划', status: 'completed', visitCount: 1, lastSequence: 3 },
      { id: 'act', label: '调用工具', detail: actDetail, status: 'running', visitCount: 1, lastSequence: 4 },
      { id: 'memory', label: '记忆提案', status: 'pending', visitCount: 0, lastSequence: 0 },
    ],
    transitions: [], visitedPath: ['receive', 'context', 'plan', 'act'], lastSequence: 4,
  }
}

describe('projectLearningPath', () => {
  it('uses retrieval tool truth for the evidence phase', () => {
    const path = projectLearningPath(projection('knowledge_search · Agent Harness'))

    expect(path.find((step) => step.id === 'evidence')).toMatchObject({
      status: 'running', sourceStageId: 'act',
    })
    expect(path.find((step) => step.id === 'practice')).toMatchObject({
      status: 'completed', sourceStageId: 'plan',
    })
  })

  it('routes non-retrieval tools to practice and never invents mastery', () => {
    const path = projectLearningPath(projection('run_shell · npm test'))

    expect(path.find((step) => step.id === 'evidence')).toMatchObject({
      status: 'pending', caption: '等待检索事实',
    })
    expect(path.find((step) => step.id === 'practice')).toMatchObject({
      status: 'running', sourceStageId: 'act',
    })
    expect(path.find((step) => step.id === 'mastery')).toEqual({
      id: 'mastery', label: '掌握度', caption: '尚未记录掌握度', status: 'pending',
    })
  })
})
