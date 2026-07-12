import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  approveCodingPlan,
  buildCodingStreamUrl,
  fetchCodingFile,
  fetchCodingFiles,
  fetchCodingApprovalPending,
  fetchCodingContext,
  fetchCodingGitStatus,
  fetchCodingMcpServers,
  fetchCodingModels,
  fetchCodingRun,
  fetchCodingRunDiff,
  fetchCodingRuns,
  fetchCodingSessionMessages,
  fetchCodingSessions,
  fetchCodingSkills,
  rejectCodingPlan,
  requestCodingCompaction,
  respondCodingApproval,
  resumeCodingSession,
  startCodingSession,
  stopCodingRun,
  switchCodingModel,
  switchPermissionMode,
} from '../api/coding'
import type {
  CodingApproval,
  CodingApprovalChoice,
  CodingDiffLine,
  CodingFileEntry,
  CodingGitStatusResponse,
  CodingMcpServer,
  CodingModel,
  CodingRunDetailResponse,
  CodingRunDiff,
  CodingRunSummary,
  CodingServerEvent,
  CodingSessionSummary,
  CodingSkillSummary,
  CodingToolResultEvent,
  CodingContextSnapshot,
  PermissionMode,
} from '../types/api'
import { applyCodingEvent } from './codingEvents'
import type { ChatMessage, DiffInfo, PlanReviewState } from './codingEvents'
import { CodingStream } from './codingStream'

export type { ChatMessage, ToolActivity } from './codingEvents'

// Noise entries hidden from the workspace file tree.
const FILE_TREE_NOISE_PATTERNS: Array<RegExp | string> = [
  /^\.env(\..*)?$/, // .env, .env.local, .env.production ...
  '.DS_Store',
  '__pycache__',
  /\.pyc$/, // compiled python
  'node_modules',
  '.git',
  /\.log$/, // log files
]

function isFileTreeNoise(name: string): boolean {
  return FILE_TREE_NOISE_PATTERNS.some((pattern) =>
    typeof pattern === 'string' ? name === pattern : pattern.test(name),
  )
}

function filterFileEntries(entries: CodingFileEntry[]): CodingFileEntry[] {
  return entries.filter((entry) => !isFileTreeNoise(entry.name))
}

function contextReasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    insufficient_history: '历史内容不足，暂不需要压缩',
    compaction_busy: '已有压缩任务正在进行',
    context_emergency: '上下文已达到安全上限',
    invalid_previous_checkpoint: '历史压缩检查点无效',
  }
  return labels[reason] || '上下文压缩未完成'
}

export const useCodingStore = defineStore('coding', () => {
  const sessionId = ref('')
  const workspaceRoot = ref('')
  const messages = ref<ChatMessage[]>([])
  const isThinking = ref(false)
  const errorMessage = ref('')
  const currentModelId = ref('')
  const contextChars = ref(0)
  const contextSnapshot = ref<CodingContextSnapshot | null>(null)
  const compactionState = ref<'idle' | 'running' | 'succeeded' | 'failed'>('idle')
  const compactionError = ref('')
  const pendingApproval = ref<CodingApproval | null>(null)
  const approvalBusy = ref(false)
  const thinkingPhase = ref('')
  const runtimeMode = ref('default')
  const permissionMode = ref<PermissionMode>('default')
  const planTopic = ref('')
  const planPath = ref('')
  const planReview = ref<PlanReviewState | null>(null)
  const lastDiffInfo = ref<DiffInfo | null>(null)
  const diffDrawerVisible = ref(false)
  const currentDiffData = ref<CodingRunDiff | null>(null)
  const codingSessions = ref<CodingSessionSummary[]>([])
  const runs = ref<CodingRunSummary[]>([])
  const selectedRun = ref<CodingRunDetailResponse | null>(null)

  const skills = ref<CodingSkillSummary[]>([])
  const mcpServers = ref<CodingMcpServer[]>([])
  const models = ref<CodingModel[]>([])
  const gitStatus = ref<CodingGitStatusResponse>({
    is_git: false,
    branch: '',
    dirty_count: 0,
    changed_files: [],
  })

  const fileTreePath = ref('.')
  const fileTreeEntries = ref<CodingFileEntry[]>([])
  const expandedDirs = ref<Set<string>>(new Set())
  const previewPath = ref('')
  const previewContent = ref('')
  const breadcrumb = computed(() => fileTreePath.value.split('/').filter(Boolean))

  let stream: CodingStream | null = null
  let approvalPollTimer: number | null = null
  let fileTreeGeneration = 0
  let contextRequestGeneration = 0
  const dirCache = new Map<string, CodingFileEntry[]>()

  const contextBudget = computed(() => contextSnapshot.value?.effective_limit_tokens ?? 0)
  const contextConfigured = computed(() => contextSnapshot.value?.configured ?? false)
  const contextCompactable = computed(() => contextSnapshot.value?.compactable ?? false)
  const contextBusy = computed(
    () =>
      compactionState.value === 'running' ||
      contextSnapshot.value?.context_operation_active === true,
  )
  const contextPercent = computed(() => {
    const limit = contextSnapshot.value?.effective_limit_tokens ?? 0
    if (limit <= 0) return 0
    return Math.min(100, Math.round((contextChars.value / limit) * 100))
  })

  async function initialize() {
    if (sessionId.value) return
    const session = await startCodingSession()
    sessionId.value = session.session_id
    workspaceRoot.value = session.workspace_root
    permissionMode.value = session.permission_mode
    await Promise.all([
      loadSkills(),
      loadMcpServers(),
      loadModels(),
      loadContext(),
      loadGitStatus(),
      loadFiles('.'),
      loadSessions(),
      loadRuns(),
    ])
    connectSocket()
  }

  function connectSocket() {
    if (!sessionId.value) return
    stream?.disconnect()
    stream = new CodingStream({
      onEvent: handleServerEvent,
      onError: (message) => {
        errorMessage.value = message
        isThinking.value = false
        thinkingPhase.value = ''
      },
    })
    stream.connect(sessionId.value, buildCodingStreamUrl(sessionId.value))
  }

  function handleServerEvent(event: CodingServerEvent) {
    const effect = applyCodingEvent(
      {
        sessionId,
        messages,
        isThinking,
        errorMessage,
        contextChars,
        contextSnapshot,
        compactionState,
        compactionError,
        pendingApproval,
        thinkingPhase,
        runtimeMode,
        planTopic,
        planPath,
        planReview,
        lastDiffInfo,
      },
      event,
    )
    if (event.type === 'turn_finished') {
      window.setTimeout(() => void loadContext(), 0)
    }
    if (effect.approvalRequired) {
      void enrichApprovalPreview()
      startApprovalPolling()
      return
    }
    if (effect.toolResult) {
      void refreshWorkspaceAfterTool(effect.toolResult)
      return
    }
    if (effect.terminal) {
      stopApprovalPolling()
      if (event.type !== 'error') {
        void loadSessions()
        void loadRuns()
      }
    }
  }

  function sendMessage(content: string) {
    if (!content.trim() || !stream) return
    const sent = stream.send(content)
    if (!sent) return
    messages.value.push({ role: 'user', content })
    isThinking.value = true
    errorMessage.value = ''
    pendingApproval.value = null
    thinkingPhase.value = ''
    startApprovalPolling()
  }

  async function pollApproval() {
    if (!sessionId.value || !isThinking.value) return
    try {
      pendingApproval.value = await fetchCodingApprovalPending(sessionId.value)
    } catch {
      // WebSocket approval_required is primary; polling is a resilience layer.
    }
  }

  function startApprovalPolling() {
    if (approvalPollTimer !== null) return
    approvalPollTimer = window.setInterval(() => {
      void pollApproval()
    }, 1500)
  }

  function stopApprovalPolling() {
    if (approvalPollTimer === null) return
    window.clearInterval(approvalPollTimer)
    approvalPollTimer = null
  }

  async function enrichApprovalPreview() {
    const approval = pendingApproval.value
    if (!approval) return
    const path = typeof approval.args.path === 'string' ? approval.args.path : ''
    if (!path) return
    if (approval.tool === 'patch_file') {
      const oldText = typeof approval.args.old_text === 'string' ? approval.args.old_text : ''
      const newText = typeof approval.args.new_text === 'string' ? approval.args.new_text : ''
      pendingApproval.value = {
        ...approval,
        diff_preview: buildSimpleDiff(oldText, newText),
      }
      return
    }
    if (approval.tool !== 'write_file') return
    const content = typeof approval.args.content === 'string' ? approval.args.content : ''
    const fileExists = await workspaceFileExists(path)
    if (!fileExists) {
      if (pendingApproval.value?.approval_id !== approval.approval_id) return
      pendingApproval.value = {
        ...pendingApproval.value,
        diff_preview: buildSimpleDiff('', content),
      }
      return
    }
    try {
      const current = await fetchCodingFile(sessionId.value, path)
      if (pendingApproval.value?.approval_id !== approval.approval_id) return
      pendingApproval.value = {
        ...pendingApproval.value,
        diff_preview: buildSimpleDiff(current.content, content),
      }
    } catch {
      pendingApproval.value = {
        ...approval,
        diff_preview: buildSimpleDiff('', content),
      }
    }
  }

  async function workspaceFileExists(path: string): Promise<boolean> {
    if (!sessionId.value) return false
    const segments = path.replace(/^\.\//, '').split('/')
    const name = segments.pop()
    if (!name) return false
    const parent = segments.join('/') || '.'
    try {
      const listing = await fetchCodingFiles(sessionId.value, parent)
      return listing.entries.some((entry) => entry.name === name && !entry.is_dir)
    } catch {
      // A diff preview is non-critical. Avoid requesting an unknown file and
      // presenting a browser-visible 400 while an approval is pending.
      return false
    }
  }

  function buildSimpleDiff(before: string, after: string): CodingDiffLine[] {
    const beforeLines = diffLines(before)
    const afterLines = diffLines(after)
    if (before === after) {
      return beforeLines.slice(0, 40).map((text) => ({ type: 'context', text }))
    }
    return [
      ...beforeLines.slice(0, 40).map((text) => ({ type: 'remove' as const, text })),
      ...afterLines.slice(0, 40).map((text) => ({ type: 'add' as const, text })),
    ]
  }

  function diffLines(content: string): string[] {
    if (!content) return []
    const lines = content.split('\n')
    if (content.endsWith('\n')) lines.pop()
    return lines
  }

  async function respondApproval(choice: CodingApprovalChoice) {
    if (!sessionId.value || !pendingApproval.value || approvalBusy.value) return
    const approvalId = pendingApproval.value.approval_id
    approvalBusy.value = true
    try {
      await respondCodingApproval(sessionId.value, approvalId, choice)
      pendingApproval.value = null
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : String(error)
    } finally {
      approvalBusy.value = false
    }
  }

  async function stopCurrentRun() {
    if (!sessionId.value || !isThinking.value) return
    await stopCodingRun(sessionId.value)
    pendingApproval.value = null
  }

  async function approvePlan() {
    if (!planReview.value) return
    try {
      const result = await approveCodingPlan(sessionId.value)
      // The REST call runs outside run_turn, so runtime_mode_changed is not
      // pushed over WebSocket. Apply the response locally.
      if (result.mode === 'default') {
        runtimeMode.value = 'default'
        planTopic.value = ''
        planPath.value = ''
        planReview.value = null
      }
    } catch (e) {
      errorMessage.value = String(e)
    }
  }

  async function rejectPlan() {
    if (!planReview.value) return
    try {
      await rejectCodingPlan(sessionId.value)
      // Backend stays in plan mode (no runtime_mode_changed), so clear locally.
      planReview.value = null
    } catch (e) {
      errorMessage.value = String(e)
    }
  }

  async function loadSkills() {
    try {
      const res = await fetchCodingSkills()
      skills.value = res.skills
    } catch {
      skills.value = []
    }
  }

  async function loadMcpServers() {
    try {
      const res = await fetchCodingMcpServers()
      mcpServers.value = res.servers
    } catch {
      mcpServers.value = []
    }
  }

  async function loadModels() {
    try {
      const res = await fetchCodingModels()
      models.value = res.models
      if (res.current) currentModelId.value = res.current
      else if (res.models.length > 0) currentModelId.value = res.models[0].id
    } catch {
      models.value = []
    }
  }

  async function loadContext() {
    if (!sessionId.value) return
    const targetSessionId = sessionId.value
    const requestGeneration = ++contextRequestGeneration
    try {
      const snapshot = await fetchCodingContext(targetSessionId)
      if (targetSessionId !== sessionId.value || requestGeneration !== contextRequestGeneration) return
      contextSnapshot.value = snapshot
      contextChars.value = contextSnapshot.value.used_tokens ?? 0
      if (snapshot.model_id) currentModelId.value = snapshot.model_id
    } catch {
      if (targetSessionId === sessionId.value && requestGeneration === contextRequestGeneration) {
        contextSnapshot.value = null
      }
    }
  }

  async function loadRuns() {
    if (!sessionId.value) return
    try {
      const res = await fetchCodingRuns(sessionId.value)
      runs.value = res.runs
    } catch {
      runs.value = []
    }
  }

  async function loadSessions() {
    try {
      const res = await fetchCodingSessions()
      codingSessions.value = res.sessions
    } catch {
      codingSessions.value = []
    }
  }

  async function selectSession(targetSessionId: string) {
    if (!targetSessionId || targetSessionId === sessionId.value) return
    stopApprovalPolling()
    stream?.disconnect()
    stream = null
    contextRequestGeneration += 1
    contextSnapshot.value = null
    contextChars.value = 0
    compactionState.value = 'idle'
    compactionError.value = ''
    const session = await resumeCodingSession(targetSessionId)
    sessionId.value = session.session_id
    workspaceRoot.value = session.workspace_root
    permissionMode.value = session.permission_mode
    messages.value = await loadSessionMessages(session.session_id)
    isThinking.value = false
    errorMessage.value = ''
    pendingApproval.value = null
    thinkingPhase.value = ''
    planReview.value = null
    runtimeMode.value = 'default'
    planTopic.value = ''
    planPath.value = ''
    runs.value = []
    selectedRun.value = null
    lastDiffInfo.value = null
    diffDrawerVisible.value = false
    currentDiffData.value = null
    dirCache.clear()
    await Promise.all([loadGitStatus(), loadFiles('.', true), loadSessions(), loadRuns(), loadContext()])
    connectSocket()
  }

  async function startNewSession() {
    stopApprovalPolling()
    stream?.disconnect()
    stream = null
    contextRequestGeneration += 1
    contextSnapshot.value = null
    contextChars.value = 0
    compactionState.value = 'idle'
    compactionError.value = ''
    const session = await startCodingSession()
    sessionId.value = session.session_id
    workspaceRoot.value = session.workspace_root
    permissionMode.value = session.permission_mode
    messages.value = []
    isThinking.value = false
    errorMessage.value = ''
    pendingApproval.value = null
    thinkingPhase.value = ''
    planReview.value = null
    runtimeMode.value = 'default'
    planTopic.value = ''
    planPath.value = ''
    runs.value = []
    selectedRun.value = null
    lastDiffInfo.value = null
    diffDrawerVisible.value = false
    currentDiffData.value = null
    dirCache.clear()
    await Promise.all([loadGitStatus(), loadFiles('.', true), loadSessions(), loadRuns(), loadContext()])
    connectSocket()
  }

  async function loadSessionMessages(targetSessionId: string): Promise<ChatMessage[]> {
    try {
      const res = await fetchCodingSessionMessages(targetSessionId)
      return res.messages.map((message) => ({
        role: message.role,
        content: message.content,
      }))
    } catch {
      return []
    }
  }

  async function loadRunDetail(runId: string) {
    if (!sessionId.value) return
    try {
      selectedRun.value = await fetchCodingRun(sessionId.value, runId)
    } catch {
      selectedRun.value = null
    }
  }

  async function loadRunDiff(runId: string) {
    if (!sessionId.value) return
    try {
      currentDiffData.value = await fetchCodingRunDiff(sessionId.value, runId)
      diffDrawerVisible.value = true
    } catch (e) {
      errorMessage.value = String(e)
    }
  }

  function openDiffDrawer() {
    if (lastDiffInfo.value) {
      void loadRunDiff(lastDiffInfo.value.run_id)
    }
  }

  function closeDiffDrawer() {
    diffDrawerVisible.value = false
  }

  async function loadGitStatus() {
    if (!sessionId.value) return
    try {
      gitStatus.value = await fetchCodingGitStatus(sessionId.value)
    } catch {
      gitStatus.value = { is_git: false, branch: '', dirty_count: 0, changed_files: [] }
    }
  }

  async function loadFiles(path: string, force = false) {
    if (!sessionId.value) return
    const generation = ++fileTreeGeneration
    if (!force && dirCache.has(path)) {
      fileTreePath.value = path
      fileTreeEntries.value = filterFileEntries([...(dirCache.get(path) || [])])
      expandedDirs.value = new Set([...expandedDirs.value, path])
      return
    }
    try {
      const res = await fetchCodingFiles(sessionId.value, path)
      if (generation !== fileTreeGeneration) return
      const entries = filterFileEntries(res.entries)
      dirCache.set(path, entries)
      fileTreePath.value = path
      fileTreeEntries.value = entries
      expandedDirs.value = new Set([...expandedDirs.value, path])
    } catch {
      if (generation !== fileTreeGeneration) return
      fileTreeEntries.value = []
    }
  }

  async function refreshWorkspaceView() {
    dirCache.clear()
    await Promise.all([loadFiles(fileTreePath.value, true), loadGitStatus()])
  }

  async function refreshWorkspaceAfterTool(event: CodingToolResultEvent) {
    if (event.is_error) return
    if (!['write_file', 'patch_file', 'run_shell'].includes(event.tool)) return
    await refreshWorkspaceView()
  }

  async function loadFilePreview(path: string) {
    if (!sessionId.value) return
    try {
      const res = await fetchCodingFile(sessionId.value, path)
      previewPath.value = path
      previewContent.value = res.content
    } catch {
      previewPath.value = path
      previewContent.value = '无法加载文件'
    }
  }

  async function changeModel(modelId: string) {
    if (!sessionId.value) return
    try {
      await switchCodingModel(sessionId.value, modelId)
      currentModelId.value = modelId
      await loadContext()
    } catch (error) {
      errorMessage.value = String(error)
    }
  }

  async function compactContext(): Promise<boolean> {
    if (!sessionId.value || isThinking.value || contextBusy.value || !contextCompactable.value) {
      return false
    }
    compactionState.value = 'running'
    compactionError.value = ''
    const targetSessionId = sessionId.value
    const requestGeneration = contextRequestGeneration
    try {
      const response = await requestCodingCompaction(targetSessionId)
      if (targetSessionId !== sessionId.value || requestGeneration !== contextRequestGeneration) return false
      contextSnapshot.value = response.context
      contextChars.value = response.after_tokens
      compactionState.value = response.applied ? 'succeeded' : 'failed'
      if (!response.applied) compactionError.value = contextReasonLabel(response.reason)
      return response.applied
    } catch (error) {
      if (targetSessionId !== sessionId.value || requestGeneration !== contextRequestGeneration) return false
      compactionState.value = 'failed'
      compactionError.value = String(error)
      errorMessage.value = compactionError.value
      return false
    }
  }

  async function changePermissionMode(mode: PermissionMode): Promise<boolean> {
    if (!sessionId.value || isThinking.value) return false
    try {
      const result = await switchPermissionMode(sessionId.value, mode)
      permissionMode.value = result.mode
      return true
    } catch (error) {
      errorMessage.value = String(error)
      return false
    }
  }

  function disconnect() {
    stream?.disconnect()
    stream = null
    stopApprovalPolling()
  }

  return {
    sessionId,
    workspaceRoot,
    messages,
    isThinking,
    errorMessage,
    currentModelId,
    contextChars,
    contextSnapshot,
    contextBudget,
    contextConfigured,
    contextCompactable,
    contextBusy,
    contextPercent,
    compactionState,
    compactionError,
    pendingApproval,
    approvalBusy,
    thinkingPhase,
    runtimeMode,
    permissionMode,
    planTopic,
    planPath,
    planReview,
    lastDiffInfo,
    diffDrawerVisible,
    currentDiffData,
    codingSessions,
    runs,
    selectedRun,
    skills,
    mcpServers,
    models,
    gitStatus,
    fileTreePath,
    fileTreeEntries,
    expandedDirs,
    previewPath,
    previewContent,
    breadcrumb,
    initialize,
    sendMessage,
    handleServerEvent,
    respondApproval,
    stopCurrentRun,
    approvePlan,
    rejectPlan,
    selectSession,
    startNewSession,
    connectSocket,
    loadSkills,
    loadMcpServers,
    loadModels,
    loadContext,
    loadSessions,
    loadRuns,
    loadRunDetail,
    loadRunDiff,
    openDiffDrawer,
    closeDiffDrawer,
    loadGitStatus,
    loadFiles,
    refreshWorkspaceView,
    loadFilePreview,
    changeModel,
    compactContext,
    changePermissionMode,
    disconnect,
  }
})
