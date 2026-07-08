import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  buildCodingStreamUrl,
  fetchCodingFile,
  fetchCodingFiles,
  fetchCodingApprovalPending,
  fetchCodingGitStatus,
  fetchCodingMcpServers,
  fetchCodingModels,
  fetchCodingRun,
  fetchCodingRuns,
  fetchCodingSessionMessages,
  fetchCodingSessions,
  fetchCodingSkills,
  respondCodingApproval,
  resumeCodingSession,
  startCodingSession,
  stopCodingRun,
  switchCodingModel,
} from '../api/coding'
import type {
  CodingApproval,
  CodingApprovalChoice,
  CodingApprovalRequiredEvent,
  CodingDiffLine,
  CodingFileEntry,
  CodingGitStatusResponse,
  CodingMcpServer,
  CodingModel,
  CodingRunDetailResponse,
  CodingRunSummary,
  CodingServerEvent,
  CodingSessionSummary,
  CodingSkillSummary,
  CodingToolCallEvent,
  CodingToolResultEvent,
} from '../types/api'

export type ToolActivity = {
  tool: string
  args: Record<string, unknown>
  status: 'running' | 'done' | 'error'
  content: string
  durationMs?: number
}

export type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
  tools?: ToolActivity[]
  isThinking?: boolean
}

export const useCodingStore = defineStore('coding', () => {
  const sessionId = ref('')
  const workspaceRoot = ref('')
  const messages = ref<ChatMessage[]>([])
  const isThinking = ref(false)
  const errorMessage = ref('')
  const currentModelId = ref('')
  const contextChars = ref(0)
  const contextBudget = 60000
  const pendingApproval = ref<CodingApproval | null>(null)
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

  let socket: WebSocket | null = null
  let approvalPollTimer: number | null = null
  let fileTreeGeneration = 0
  const dirCache = new Map<string, CodingFileEntry[]>()

  const contextPercent = computed(() =>
    Math.min(100, Math.round((contextChars.value / contextBudget) * 100)),
  )

  async function initialize() {
    if (sessionId.value) return
    const session = await startCodingSession()
    sessionId.value = session.session_id
    workspaceRoot.value = session.workspace_root
    await Promise.all([
      loadSkills(),
      loadMcpServers(),
      loadModels(),
      loadGitStatus(),
      loadFiles('.'),
      loadSessions(),
      loadRuns(),
    ])
    connectSocket()
  }

  function connectSocket() {
    if (!sessionId.value) return
    socket?.close()
    socket = new WebSocket(buildCodingStreamUrl(sessionId.value))
    socket.onmessage = (event) => {
      handleServerEvent(JSON.parse(event.data) as CodingServerEvent)
    }
    socket.onerror = () => {
      errorMessage.value = '连接中断'
      isThinking.value = false
    }
  }

  function handleServerEvent(event: CodingServerEvent) {
    if (event.type === 'model_requested') {
      if (event.prompt_chars) contextChars.value = event.prompt_chars
      return
    }
    if (event.type === 'tool_call') {
      appendToolActivity(event as CodingToolCallEvent)
      return
    }
    if (event.type === 'approval_required') {
      setApprovalFromEvent(event as CodingApprovalRequiredEvent)
      void enrichApprovalPreview()
      startApprovalPolling()
      return
    }
    if (event.type === 'tool_result') {
      updateToolActivity(event as CodingToolResultEvent)
      void refreshWorkspaceAfterTool(event as CodingToolResultEvent)
      return
    }
    if (event.type === 'final' || event.type === 'step_limit' || event.type === 'cancelled') {
      finalizeCurrentMessage(event.content)
      isThinking.value = false
      stopApprovalPolling()
      void loadSessions()
      void loadRuns()
      return
    }
    if (event.type === 'error') {
      errorMessage.value = event.message
      isThinking.value = false
      stopApprovalPolling()
    }
  }

  function setApprovalFromEvent(event: CodingApprovalRequiredEvent) {
    pendingApproval.value = {
      approval_id: event.approval_id,
      session_id: sessionId.value,
      tool: event.tool,
      args: event.args,
      description: event.description,
      pattern_key: event.pattern_key,
    }
  }

  function appendToolActivity(event: CodingToolCallEvent) {
    const lastMessage = messages.value[messages.value.length - 1]
    if (!lastMessage || lastMessage.role !== 'assistant' || !lastMessage.isThinking) {
      messages.value.push({
        role: 'assistant',
        content: '',
        tools: [],
        isThinking: true,
      })
    }
    const current = messages.value[messages.value.length - 1]
    current.tools = current.tools || []
    current.tools.push({
      tool: event.tool,
      args: event.args,
      status: 'running',
      content: '',
    })
  }

  function updateToolActivity(event: CodingToolResultEvent) {
    for (const msg of [...messages.value].reverse()) {
      if (!msg.tools) continue
      const target = [...msg.tools].reverse().find(
        (t) => t.tool === event.tool && t.status === 'running',
      )
      if (target) {
        target.status = event.is_error ? 'error' : 'done'
        target.content = event.content.slice(0, 2000)
        return
      }
    }
  }

  function finalizeCurrentMessage(content: string) {
    const lastMessage = messages.value[messages.value.length - 1]
    if (lastMessage && lastMessage.isThinking) {
      lastMessage.content = content
      lastMessage.isThinking = false
    } else {
      messages.value.push({ role: 'assistant', content })
    }
  }

  function sendMessage(content: string) {
    if (!content.trim() || !socket || socket.readyState !== WebSocket.OPEN) return
    messages.value.push({ role: 'user', content })
    isThinking.value = true
    errorMessage.value = ''
    pendingApproval.value = null
    startApprovalPolling()
    socket.send(JSON.stringify({ content }))
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

  function buildSimpleDiff(before: string, after: string): CodingDiffLine[] {
    const beforeLines = before.split('\n')
    const afterLines = after.split('\n')
    if (before.endsWith('\n')) beforeLines.pop()
    if (after.endsWith('\n')) afterLines.pop()
    if (before === after) {
      return beforeLines.slice(0, 40).map((text) => ({ type: 'context', text }))
    }
    return [
      ...beforeLines.slice(0, 40).map((text) => ({ type: 'remove' as const, text })),
      ...afterLines.slice(0, 40).map((text) => ({ type: 'add' as const, text })),
    ]
  }

  async function respondApproval(choice: CodingApprovalChoice) {
    if (!sessionId.value || !pendingApproval.value) return
    const approvalId = pendingApproval.value.approval_id
    await respondCodingApproval(sessionId.value, approvalId, choice)
    pendingApproval.value = null
  }

  async function stopCurrentRun() {
    if (!sessionId.value || !isThinking.value) return
    await stopCodingRun(sessionId.value)
    pendingApproval.value = null
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
    socket?.close()
    socket = null
    const session = await resumeCodingSession(targetSessionId)
    sessionId.value = session.session_id
    workspaceRoot.value = session.workspace_root
    messages.value = await loadSessionMessages(session.session_id)
    isThinking.value = false
    errorMessage.value = ''
    pendingApproval.value = null
    runs.value = []
    selectedRun.value = null
    dirCache.clear()
    await Promise.all([loadGitStatus(), loadFiles('.', true), loadSessions(), loadRuns()])
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
      fileTreeEntries.value = [...(dirCache.get(path) || [])]
      expandedDirs.value = new Set([...expandedDirs.value, path])
      return
    }
    try {
      const res = await fetchCodingFiles(sessionId.value, path)
      if (generation !== fileTreeGeneration) return
      dirCache.set(path, res.entries)
      fileTreePath.value = path
      fileTreeEntries.value = res.entries
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
    await switchCodingModel(sessionId.value, modelId)
    currentModelId.value = modelId
  }

  function disconnect() {
    socket?.close()
    socket = null
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
    contextBudget,
    contextPercent,
    pendingApproval,
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
    selectSession,
    loadSkills,
    loadMcpServers,
    loadModels,
    loadSessions,
    loadRuns,
    loadRunDetail,
    loadGitStatus,
    loadFiles,
    refreshWorkspaceView,
    loadFilePreview,
    changeModel,
    disconnect,
  }
})
