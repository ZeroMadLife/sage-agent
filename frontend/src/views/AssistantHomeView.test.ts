import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import AssistantHomeView from './AssistantHomeView.vue'
import { useAssistantHomeStore } from '../stores/assistantHome'
import { useCodingStore } from '../stores/coding'
import type {
  AssistantHomeSummary,
  LearningActivationResponse,
  LearningTaskResponse,
} from '../types/api'

function summary(): AssistantHomeSummary {
  return {
    identity: { mode: 'local', user_id: null, display_name: '本地工作区' },
    knowledge: { status: 'not_configured', source_count: 0, wiki_page_count: 0, last_synced_at: null },
    sessions: {
      status: 'ready', total: 1, error: null,
      items: [{
        session_id: 'recent', title: '复盘 Sage', workspace_name: 'tour-agent',
        updated_at: '2026-07-15T00:00:00Z', message_count: 4,
        target: '/coding/session/recent',
      }],
    },
    projects: { status: 'empty', items: [], total: 0, error: null },
    proposals: { status: 'ready', memory_pending: 1, wiki_pending: 0, note_pending: 0, error: null },
    suggested_actions: [{
      id: 'review-memory', kind: 'review', label: '查看待确认沉淀',
      description: '有 1 条记忆提案等待处理。', target: '/growth',
    }],
  }
}

function learningTask(
  status: LearningTaskResponse['status'] = 'draft',
): LearningTaskResponse {
  return {
    version: 1,
    workspace_id: 'workspace-1',
    task_id: 'ltask-1',
    task_revision: 2,
    template_id: 'general_learning',
    topic: '学习 Timeline 与 Resume',
    desired_outcome: '能够解释恢复边界',
    learner_profile: {
      starting_level: 'beginner',
      time_budget_minutes_per_week: 180,
      target_date: null,
    },
    source_policy: {
      knowledge: 'preferred',
      web: 'forbidden',
      domains: [],
      freshness: 'all',
    },
    risk_class: 'general_education',
    risk_notice: null,
    clarification: { required_fields: [], questions: [], ready_to_activate: true },
    learning_plan_id: null,
    learning_plan_hash: null,
    dag_hash: null,
    learning_goal_ref: status === 'active' ? { goal_id: 'goal-1', goal_revision: 'g1' } : null,
    status,
    created_at: '2026-08-24T00:00:00Z',
    updated_at: '2026-08-24T00:01:00Z',
  }
}

function activationReceipt(): LearningActivationResponse {
  return {
    version: 3,
    workspace_id: 'workspace-1',
    task_id: 'ltask-1',
    task_revision: 2,
    session_id: 'learning-session',
    thread_goal_revision: 1,
    learning_goal_ref: { goal_id: 'goal-1', goal_revision: 'g1' },
    learning_plan_id: null,
    learning_plan_hash: null,
    turn_context_plan_id: 'turnplan-1',
    turn_context_plan_hash: 'sha256:plan',
    dag_hash: null,
    plan_id: 'turnplan-1',
    plan_hash: 'sha256:plan',
    catalog_revision: 'catalog-1',
    capability_revision: 'capability-1',
    allowed_capabilities: ['local:knowledge_search'],
    source_policy_snapshot: learningTask().source_policy,
    source_policy_revision: 'source-1',
    resume_validation_version: 'canonical_l0_v3',
    receipt_status: 'active',
    failure_code: null,
    created_at: '2026-08-24T00:00:00Z',
    updated_at: '2026-08-24T00:00:01Z',
    completed_at: '2026-08-24T00:00:01Z',
  }
}

function kickoffReceipt() {
  return {
    version: 1 as const,
    workspace_id: 'workspace-1', task_id: 'ltask-1', task_revision: 2,
    activation_idempotency_key_hash: 'sha256:activation',
    kickoff_idempotency_key_hash: 'sha256:kickoff', dispatch_id: 'lkick-1',
    session_id: 'learning-session', message_id: 'learning-kickoff:1',
    acceptance_run_id: 'run_learning_accept_1', turn_run_id: 'run_learning_turn_1',
    content_hash: 'sha256:content', receipt_status: 'accepted' as const,
    stage: 'accepted' as const, created_at: '2026-08-24T00:00:01Z',
    updated_at: '2026-08-24T00:00:02Z', accepted_at: '2026-08-24T00:00:02Z',
  }
}

async function mountHome() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/assistant', component: AssistantHomeView },
      { path: '/coding', component: { template: '<div />' } },
      { path: '/coding/session/:sessionId', component: { template: '<div />' } },
      { path: '/knowledge', component: { template: '<div />' } },
      { path: '/growth', component: { template: '<div />' } },
      { path: '/public', component: { template: '<div />' } },
      { path: '/settings/appearance', component: { template: '<div />' } },
    ],
  })
  await router.push('/assistant')
  const wrapper = mount(AssistantHomeView, {
    global: { plugins: [router] },
    attachTo: document.body,
  })
  return { router, wrapper }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 })
})

it('loads a real summary without creating a coding session on mount', async () => {
  const home = useAssistantHomeStore()
  const coding = useCodingStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  home.learningState = 'draft'
  home.restoreLearningTask = vi.fn().mockResolvedValue(undefined)
  coding.startSessionWithPrompt = vi.fn()

  const { wrapper } = await mountHome()

  expect(home.load).toHaveBeenCalledTimes(1)
  expect(home.restoreLearningTask).toHaveBeenCalledTimes(1)
  expect(coding.startSessionWithPrompt).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('今天想推进什么？')
  expect(wrapper.text()).toContain('复盘 Sage')
  expect(wrapper.text()).toContain('继续最近对话')
  expect(wrapper.get('a[aria-label="导入知识库"]').attributes('href')).toContain('/knowledge')
  expect(wrapper.text()).toContain('1 条待确认沉淀')
  expect(wrapper.find('#projects-title').exists()).toBe(false)
  wrapper.unmount()
})

it('keeps direct Assistant chat compatible with startSessionWithPrompt', async () => {
  const home = useAssistantHomeStore()
  const coding = useCodingStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  home.learningState = 'draft'
  home.restoreLearningTask = vi.fn().mockResolvedValue(undefined)
  coding.startSessionWithPrompt = vi.fn().mockResolvedValue('new-session')
  const { router, wrapper } = await mountHome()

  await wrapper.get('button[aria-label="直接对话模式"]').trigger('click')
  await wrapper.get('textarea[aria-label="给 Sage 的消息"]').setValue('学习 timeline replay')
  await wrapper.get('button[aria-label="发送消息"]').trigger('click')

  expect(coding.startSessionWithPrompt).toHaveBeenCalledTimes(1)
  expect(coding.startSessionWithPrompt).toHaveBeenCalledWith('学习 timeline replay')
  await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/coding/session/new-session'))
  expect(localStorage.getItem('sage.coding.recentSessionId')).toBe('new-session')
  wrapper.unmount()
})

it('preserves the prompt and shows a visible error when session creation fails', async () => {
  const home = useAssistantHomeStore()
  const coding = useCodingStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  home.learningState = 'draft'
  home.restoreLearningTask = vi.fn().mockResolvedValue(undefined)
  coding.startSessionWithPrompt = vi.fn().mockRejectedValue(new Error('服务不可用'))
  const { wrapper } = await mountHome()
  const textarea = wrapper.get('textarea[aria-label="给 Sage 的消息"]')

  await wrapper.get('button[aria-label="直接对话模式"]').trigger('click')
  await textarea.setValue('不要丢失这段草稿')
  await wrapper.get('button[aria-label="发送消息"]').trigger('click')

  expect(wrapper.get('[role="alert"]').text()).toContain('服务不可用')
  expect((textarea.element as HTMLTextAreaElement).value).toBe('不要丢失这段草稿')
  wrapper.unmount()
})

it('focuses the composer when a suggested action targets compose', async () => {
  const home = useAssistantHomeStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  home.learningState = 'draft'
  home.restoreLearningTask = vi.fn().mockResolvedValue(undefined)
  const { router, wrapper } = await mountHome()

  await router.push('/assistant?action=compose')
  const textarea = wrapper.get('textarea[aria-label="给 Sage 的消息"]')
  await vi.waitFor(() => expect(document.activeElement).toBe(textarea.element))
  wrapper.unmount()
})

it('creates a learning draft before showing confirmation details', async () => {
  const home = useAssistantHomeStore()
  const coding = useCodingStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  home.learningState = 'draft'
  home.restoreLearningTask = vi.fn().mockResolvedValue(undefined)
  home.createLearningTaskDraft = vi.fn().mockImplementation(async () => {
    home.learningTask = learningTask()
    home.learningState = 'needs_confirmation'
    return home.learningTask
  })
  coding.startSessionWithPrompt = vi.fn()
  const { wrapper } = await mountHome()

  await wrapper.get('textarea[aria-label="给 Sage 的消息"]').setValue('学习 Timeline 与 Resume')
  await wrapper.get('button[aria-label="创建学习任务"]').trigger('click')

  expect(home.createLearningTaskDraft).toHaveBeenCalledWith({ topic: '学习 Timeline 与 Resume' })
  expect(coding.startSessionWithPrompt).not.toHaveBeenCalled()
  await vi.waitFor(() => expect(wrapper.text()).toContain('确认学习任务'))
  expect((wrapper.get('textarea[aria-label="学习结果"]').element as HTMLTextAreaElement).value)
    .toBe('能够解释恢复边界')
  expect(wrapper.text()).toContain('优先使用本地知识，不联网')
  expect(wrapper.text()).toContain('确认前不会启动运行时')
  wrapper.unmount()
})

it('does not enter the shared session before both activation and kickoff receipts are accepted', async () => {
  const home = useAssistantHomeStore()
  const coding = useCodingStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  home.restoreLearningTask = vi.fn().mockResolvedValue(undefined)
  home.learningTask = learningTask()
  home.learningState = 'needs_confirmation'
  home.updateCurrentLearningTask = vi.fn().mockResolvedValue(home.learningTask)
  let resolveActivation!: (receipt: LearningActivationResponse) => void
  home.activateCurrentLearningTask = vi.fn().mockReturnValue(
    new Promise((resolve) => { resolveActivation = resolve }),
  )
  let resolveKickoff!: (receipt: ReturnType<typeof kickoffReceipt>) => void
  home.dispatchCurrentLearningKickoff = vi.fn().mockReturnValue(
    new Promise((resolve) => { resolveKickoff = resolve }),
  )
  coding.selectSession = vi.fn().mockResolvedValue(undefined)
  const { router, wrapper } = await mountHome()

  await wrapper.get('button[aria-label="确认并进入学习会话"]').trigger('click')
  expect(home.activateCurrentLearningTask).toHaveBeenCalledTimes(1)
  expect(home.dispatchCurrentLearningKickoff).not.toHaveBeenCalled()
  expect(coding.selectSession).not.toHaveBeenCalled()

  resolveActivation(activationReceipt())
  await vi.waitFor(() => expect(home.dispatchCurrentLearningKickoff).toHaveBeenCalledTimes(1))
  expect(coding.selectSession).not.toHaveBeenCalled()
  expect(router.currentRoute.value.fullPath).toBe('/assistant')

  resolveKickoff(kickoffReceipt())
  await vi.waitFor(() => expect(coding.selectSession).toHaveBeenCalledWith('learning-session'))
  await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/coding/session/learning-session'))
  wrapper.unmount()
})

it('shows activating, failed retry, and refresh-restored active states', async () => {
  const home = useAssistantHomeStore()
  const coding = useCodingStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  home.restoreLearningTask = vi.fn().mockResolvedValue(undefined)
  home.learningTask = learningTask('activation_failed')
  home.learningState = 'activation_failed'
  home.learningError = 'bootstrap 暂时失败'
  home.activateCurrentLearningTask = vi.fn().mockResolvedValue(activationReceipt())
  coding.selectSession = vi.fn().mockResolvedValue(undefined)
  const { wrapper } = await mountHome()

  expect(wrapper.text()).toContain('bootstrap 暂时失败')
  expect(wrapper.find('button[aria-label="重试激活"]').exists()).toBe(true)

  home.learningState = 'activating'
  await vi.waitFor(() => expect(wrapper.text()).toContain('刷新激活状态'))

  home.learningState = 'active'
  home.activationReceipt = activationReceipt()
  home.kickoffReceipt = kickoffReceipt()
  await vi.waitFor(() => expect(wrapper.find('button[aria-label="继续学习会话"]').exists()).toBe(true))
  await wrapper.get('button[aria-label="继续学习会话"]').trigger('click')
  expect(coding.selectSession).toHaveBeenCalledWith('learning-session')
  wrapper.unmount()
})

it('shows receipt recovery and activating refresh actions instead of an unusable active CTA', async () => {
  const home = useAssistantHomeStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  home.restoreLearningTask = vi.fn().mockResolvedValue(undefined)
  home.learningTask = learningTask('active')
  home.learningState = 'receipt_recovery_failed'
  home.learningError = '激活凭据恢复失败'
  const { wrapper } = await mountHome()

  expect(wrapper.find('button[aria-label="继续学习会话"]').exists()).toBe(false)
  expect(wrapper.find('button[aria-label="重试恢复凭据"]').exists()).toBe(true)
  await wrapper.get('button[aria-label="重试恢复凭据"]').trigger('click')
  expect(home.restoreLearningTask).toHaveBeenCalledWith(true)

  home.learningState = 'activating'
  await vi.waitFor(() => expect(wrapper.find('button[aria-label="刷新激活状态"]').exists()).toBe(true))
  wrapper.unmount()
})

it('shows loading while restoring the server task state', async () => {
  const home = useAssistantHomeStore()
  home.summary = summary()
  home.load = vi.fn().mockResolvedValue(undefined)
  home.learningState = 'loading'
  home.restoreLearningTask = vi.fn().mockResolvedValue(undefined)

  const { wrapper } = await mountHome()

  expect(wrapper.text()).toContain('正在恢复学习任务')
  wrapper.unmount()
})
