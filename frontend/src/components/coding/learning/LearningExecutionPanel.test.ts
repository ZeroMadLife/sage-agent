import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import type { LearningArtifactResponse, LearningResumeResponse } from '../../../types/api'
import LearningExecutionPanel from './LearningExecutionPanel.vue'

const fetchLearningResume = vi.fn()
const fetchLearningArtifact = vi.fn()
const advanceLearningTask = vi.fn()

vi.mock('../../../api/assistant', () => ({
  fetchLearningResume: (...args: unknown[]) => fetchLearningResume(...args),
  fetchLearningArtifact: (...args: unknown[]) => fetchLearningArtifact(...args),
  advanceLearningTask: (...args: unknown[]) => advanceLearningTask(...args),
  isLearningRequestStatus: () => false,
}))

function resume(stage: LearningResumeResponse['stage'] = 'artifact_ready'): LearningResumeResponse {
  return {
    task_id: 'ltask-1', task_revision: 2, goal_summary: '学习 checkpoint',
    plan_id: 'lplan-1', plan_hash: 'sha256:plan', dag_hash: 'sha256:dag', stage,
    evidence_count: 1, citation_count: 1, gap_codes: [], blocking_reason: '',
    next_action: stage === 'artifact_ready' ? 'review_artifact' : 'synthesize',
    artifact_ref: 'sage://learning/artifacts/lart-1',
    artifact: {
      artifact_id: 'lart-1', kind: 'learning_map', content_hash: 'sha256:content',
      media_type: 'text/markdown', status: 'ready', citation_count: 1,
      source_revisions: ['source-r1'], retention: 'task',
    },
    checkpoint_revision: 4, fencing_token: 3,
  }
}

function artifact(): LearningArtifactResponse {
  return {
    artifact_id: 'lart-1', artifact_ref: 'sage://learning/artifacts/lart-1',
    schema_version: 1, kind: 'learning_map', task_id: 'ltask-1', task_revision: 2,
    goal_id: 'goal-1', goal_revision: 'goal-r1', plan_id: 'lplan-1', plan_revision: 1,
    unit_ids: ['lunit-1'],
    content_hash: 'sha256:content', media_type: 'text/markdown', status: 'ready',
    evidence_refs: ['wcite-1'], source_revisions: ['source-r1'], retention: 'task',
    research_receipt_ref: 'sage://learning/research-receipts/lrsearch-1',
    content: '# 学习地图\n\n来源状态：已支持。\n',
    citations: [{
      evidence_ref: 'wcite-1', title: 'Checkpoint docs',
      url: 'https://docs.example.com/checkpoint', content_hash: 'sha256:web',
      fetched_at: '2026-08-25T01:00:00Z', page_revision: 'page-r1',
      source_revision: 'source-r1',
    }],
    created_at: '2026-08-25T00:00:00Z', updated_at: '2026-08-25T00:00:00Z',
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  fetchLearningResume.mockResolvedValue(resume())
  fetchLearningArtifact.mockResolvedValue(artifact())
})

it('renders canonical DAG checkpoint artifact and citation after refresh', async () => {
  const wrapper = mount(LearningExecutionPanel, { props: { taskId: 'ltask-1' } })
  await flushPromises()

  expect(fetchLearningResume).toHaveBeenCalledWith('ltask-1')
  expect(fetchLearningArtifact).toHaveBeenCalledWith('ltask-1', 'lart-1')
  expect(wrapper.get('.learning-dag').attributes('data-dag-hash')).toBe('sha256:dag')
  expect(wrapper.get('.learning-dag').text()).toContain('Knowledge')
  expect(wrapper.text()).toContain('Checkpoint 4 · artifact_ready')
  expect(wrapper.text()).toContain('Artifact lart-1')
  expect(wrapper.get('a').attributes('href')).toBe('https://docs.example.com/checkpoint')
  expect(wrapper.get('pre').text()).toContain('# 学习地图')
})

it('advances exactly the current checkpoint and reloads its artifact', async () => {
  fetchLearningResume.mockResolvedValue(resume('synthesize_pending'))
  advanceLearningTask.mockResolvedValue(resume('artifact_ready'))
  const wrapper = mount(LearningExecutionPanel, { props: { taskId: 'ltask-1' } })
  await flushPromises()

  await wrapper.get('.advance-button').trigger('click')
  await flushPromises()

  expect(advanceLearningTask).toHaveBeenCalledWith(
    'ltask-1', 4, 'learning-ui-ltask-1-checkpoint-4',
  )
  expect(wrapper.text()).toContain('artifact_ready')
  expect(wrapper.find('.advance-button').exists()).toBe(false)
})
