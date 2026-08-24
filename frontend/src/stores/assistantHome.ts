import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  activateLearningTask,
  createLearningDraft,
  fetchAssistantHome,
  fetchLearningActivation,
  fetchLearningTask,
  fetchLearningTasks,
  updateLearningDraft,
} from '../api/assistant'
import type {
  AssistantHomeSummary,
  LearningActivationResponse,
  LearningTaskDraftInput,
  LearningTaskPatchInput,
  LearningTaskResponse,
} from '../types/api'

export type AssistantLearningState =
  | 'draft'
  | 'loading'
  | 'needs_confirmation'
  | 'activating'
  | 'active'
  | 'activation_failed'

export const useAssistantHomeStore = defineStore('assistantHome', () => {
  const summary = ref<AssistantHomeSummary | null>(null)
  const loading = ref(false)
  const error = ref('')
  const learningState = ref<AssistantLearningState>('loading')
  const learningTask = ref<LearningTaskResponse | null>(null)
  const activationReceipt = ref<LearningActivationResponse | null>(null)
  const learningError = ref('')
  let requestGeneration = 0
  let inFlight: Promise<void> | null = null
  let learningRequestGeneration = 0
  let learningLoadInFlight: Promise<void> | null = null
  let activationInFlight: Promise<LearningActivationResponse> | null = null

  async function load(force = false): Promise<void> {
    if (summary.value && !force) return
    if (inFlight && !force) return inFlight
    const generation = ++requestGeneration
    loading.value = true
    error.value = ''
    const request = fetchAssistantHome()
      .then((result) => {
        if (generation !== requestGeneration) return
        summary.value = result
      })
      .catch((cause: unknown) => {
        if (generation !== requestGeneration) return
        error.value = cause instanceof Error ? cause.message : '首页摘要加载失败'
      })
      .finally(() => {
        if (generation === requestGeneration) loading.value = false
        if (inFlight === request) inFlight = null
      })
    inFlight = request
    return request
  }

  function invalidate() {
    requestGeneration += 1
    summary.value = null
    loading.value = false
    error.value = ''
    inFlight = null
  }

  function projectLearningTask(task: LearningTaskResponse) {
    learningTask.value = task
    if (task.status === 'active') learningState.value = 'active'
    else if (task.status === 'activating') learningState.value = 'activating'
    else if (task.status === 'activation_failed') learningState.value = 'activation_failed'
    else learningState.value = 'needs_confirmation'
  }

  async function restoreLearningTask(force = false): Promise<void> {
    if (learningLoadInFlight && !force) return learningLoadInFlight
    const generation = ++learningRequestGeneration
    learningState.value = 'loading'
    learningError.value = ''
    const request = fetchLearningTasks()
      .then(async (tasks) => {
        if (generation !== learningRequestGeneration) return
        const task = tasks.find((candidate) => !['archived', 'completed'].includes(candidate.status))
        if (!task) {
          learningTask.value = null
          activationReceipt.value = null
          learningState.value = 'draft'
          return
        }
        projectLearningTask(task)
        if (!['active', 'activating', 'activation_failed'].includes(task.status)) {
          activationReceipt.value = null
          return
        }
        const receipt = await fetchLearningActivation(task.task_id)
        if (generation !== learningRequestGeneration) return
        activationReceipt.value = receipt
        if (receipt.receipt_status === 'active') learningState.value = 'active'
        else if (receipt.receipt_status === 'activation_failed') {
          learningState.value = 'activation_failed'
        } else learningState.value = 'activating'
      })
      .catch((cause: unknown) => {
        if (generation !== learningRequestGeneration) return
        learningError.value = cause instanceof Error ? cause.message : '学习任务恢复失败'
        if (learningTask.value) projectLearningTask(learningTask.value)
        else learningState.value = 'draft'
      })
      .finally(() => {
        if (learningLoadInFlight === request) learningLoadInFlight = null
      })
    learningLoadInFlight = request
    return request
  }

  async function createLearningTaskDraft(input: LearningTaskDraftInput) {
    learningRequestGeneration += 1
    learningState.value = 'loading'
    learningError.value = ''
    try {
      const task = await createLearningDraft(input)
      activationReceipt.value = null
      projectLearningTask(task)
      return task
    } catch (cause) {
      learningState.value = 'draft'
      learningError.value = cause instanceof Error ? cause.message : '学习任务创建失败'
      throw cause
    }
  }

  async function updateCurrentLearningTask(
    patch: Omit<LearningTaskPatchInput, 'expected_revision'>,
  ) {
    const task = learningTask.value
    if (!task) throw new Error('当前没有可编辑的学习任务')
    learningRequestGeneration += 1
    learningState.value = 'loading'
    learningError.value = ''
    try {
      const updated = await updateLearningDraft(task.task_id, {
        ...patch,
        expected_revision: task.task_revision,
      })
      activationReceipt.value = null
      projectLearningTask(updated)
      return updated
    } catch (cause) {
      projectLearningTask(task)
      learningError.value = cause instanceof Error ? cause.message : '学习任务更新失败'
      throw cause
    }
  }

  async function refreshAfterActivationFailure(
    task: LearningTaskResponse,
  ): Promise<LearningActivationResponse | null> {
    try {
      const refreshed = await fetchLearningTask(task.task_id)
      projectLearningTask(refreshed)
      if (['active', 'activating', 'activation_failed'].includes(refreshed.status)) {
        const receipt = await fetchLearningActivation(task.task_id)
        activationReceipt.value = receipt
        if (receipt.receipt_status === 'active') learningState.value = 'active'
        else if (receipt.receipt_status === 'activation_failed') {
          learningState.value = 'activation_failed'
        } else learningState.value = 'activating'
        return receipt
      }
    } catch {
      learningTask.value = task
      learningState.value = 'activation_failed'
    }
    return null
  }

  function activateCurrentLearningTask(): Promise<LearningActivationResponse> {
    if (activationReceipt.value?.receipt_status === 'active') {
      return Promise.resolve(activationReceipt.value)
    }
    if (activationInFlight) return activationInFlight
    const task = learningTask.value
    if (!task) return Promise.reject(new Error('当前没有可确认的学习任务'))
    if (!task.clarification.ready_to_activate) {
      return Promise.reject(new Error('请先完成必要澄清'))
    }
    learningState.value = 'activating'
    learningError.value = ''
    const key = `learning-${task.task_id}-r${task.task_revision}`
    const request = activateLearningTask(task.task_id, task.task_revision, key)
      .then((receipt) => {
        if (receipt.receipt_status !== 'active') {
          throw new Error('服务端尚未完成学习任务激活')
        }
        activationReceipt.value = receipt
        if (learningTask.value?.task_id === task.task_id) {
          learningTask.value = {
            ...learningTask.value,
            status: 'active',
            learning_goal_ref: receipt.learning_goal_ref,
          }
        }
        learningState.value = 'active'
        return receipt
      })
      .catch(async (cause: unknown) => {
        learningError.value = cause instanceof Error ? cause.message : '学习任务激活失败'
        const recovered = await refreshAfterActivationFailure(task)
        if (recovered?.receipt_status === 'active') {
          learningError.value = ''
          return recovered
        }
        if (learningState.value !== 'active') learningState.value = 'activation_failed'
        throw cause
      })
      .finally(() => {
        if (activationInFlight === request) activationInFlight = null
      })
    activationInFlight = request
    return request
  }

  function startAnotherLearningTask() {
    learningRequestGeneration += 1
    learningTask.value = null
    activationReceipt.value = null
    learningError.value = ''
    learningState.value = 'draft'
  }

  return {
    summary, loading, error, load, invalidate,
    learningState, learningTask, activationReceipt, learningError,
    restoreLearningTask, createLearningTaskDraft, updateCurrentLearningTask,
    activateCurrentLearningTask, startAnotherLearningTask,
  }
})
