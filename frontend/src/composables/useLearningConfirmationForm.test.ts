import { describe, expect, it } from 'vitest'
import { useLearningConfirmationForm } from './useLearningConfirmationForm'
import type { LearningTaskResponse } from '../types/api'

function task(): LearningTaskResponse {
  return {
    version: 1,
    workspace_id: 'workspace-1',
    task_id: 'ltask-1',
    task_revision: 2,
    template_id: 'general_learning',
    topic: '学习 checkpoint',
    desired_outcome: '能够解释恢复边界',
    learner_profile: {
      starting_level: 'beginner',
      time_budget_minutes_per_week: 180,
      target_date: null,
    },
    source_policy: {
      knowledge: 'preferred',
      web: 'allowed_when_insufficient',
      domains: ['docs.example.com'],
      freshness: 'all',
    },
    risk_class: 'general_education',
    risk_notice: null,
    clarification: { required_fields: [], questions: [], ready_to_activate: true },
    learning_plan_id: null,
    learning_plan_hash: null,
    dag_hash: null,
    learning_goal_ref: null,
    status: 'draft',
    created_at: '2026-08-25T00:00:00Z',
    updated_at: '2026-08-25T00:00:00Z',
  }
}

describe('useLearningConfirmationForm', () => {
  it('projects a canonical task and emits a typed normalized patch', () => {
    const form = useLearningConfirmationForm()
    form.sync(task())

    expect(form.dirty.value).toBe(false)
    expect(form.sourcePolicyLabel.value).toBe('优先使用本地知识，证据不足时允许联网')

    form.domainsText.value = ' Docs.Example.com, api.example.com, docs.example.com '
    form.desiredOutcome.value = '能够独立恢复任务'

    expect(form.dirty.value).toBe(true)
    expect(form.toPatch()).toEqual({
      topic: '学习 checkpoint',
      desired_outcome: '能够独立恢复任务',
      starting_level: 'beginner',
      time_budget_minutes_per_week: 180,
      target_date: null,
      source_policy: {
        knowledge: 'preferred',
        web: 'allowed_when_insufficient',
        domains: ['docs.example.com', 'api.example.com'],
        freshness: 'all',
      },
    })
  })
})
