import { defineStore } from 'pinia'
import { computed, ref, toRef } from 'vue'
import {
  approveKnowledgeSourceProposal as approveKnowledgeSourceProposalRequest,
  approveCodingPlan,
  approveMemoryProposal,
  buildCodingStreamUrl,
  fetchCodingFile,
  fetchCodingFiles,
  fetchCodingApprovalPending,
  fetchCodingContext,
  fetchCodingGitStatus,
  fetchCodingMcpServers,
  fetchCodingModels,
  fetchCodingProviderSettings,
  fetchCloudModelProviders,
  fetchCodingUsage,
  fetchMemoryProposals,
  fetchKnowledgeSourceProposal,
  fetchKnowledgeSourceProposals,
  fetchCodingRun,
  fetchCodingRunDiff,
  fetchCodingRuns,
  fetchCodingSessionMessages,
  fetchCodingSessions,
  fetchCodingSkills,
  fetchCodingTimeline,
  fetchCodingTimelineTail,
  fetchOlderCodingTimeline,
  rejectCodingPlan,
  rejectMemoryProposal,
  rejectKnowledgeSourceProposal as rejectKnowledgeSourceProposalRequest,
  requestCodingCompaction,
  respondCodingApproval,
  resumeCodingSession,
  startCodingSession,
  stopCodingRun,
  switchCodingModel,
  switchCodingReasoning,
  switchPermissionMode,
  updateCodingSessionMetadata,
  updateCodingProviderSettings as saveCodingProviderSettings,
} from '../api/coding'
import type {
  CodingApproval,
  CodingActiveRun,
  CodingApprovalChoice,
  CodingDiffLine,
  CodingFileEntry,
  CodingGitStatusResponse,
  CodingKnowledgeSourceProposal,
  CodingKnowledgeSourceProposalDetail,
  CodingMcpServer,
  CodingModel,
  CodingProviderSettings,
  CodingProviderSettingsUpdate,
  CodingRunDetailResponse,
  CodingRunDiff,
  CodingRunSummary,
  CodingServerEvent,
  CodingSessionSummary,
  CodingRuntimeProfile,
  CodingSkillSummary,
  CodingToolResultEvent,
  CodingTimelineEvent,
  CodingContextSnapshot,
  CodingUsageSummary,
  CloudModelProvider,
  MemoryProposal,
  PermissionMode,
} from '../types/api'
import { applyCodingEvent } from './codingEvents'
import type { ChatMessage, DiffInfo, PlanReviewState } from './codingEvents'
import { CodingStream, type CodingConnectionState } from './codingStream'
import {
  createTimelineProjection,
  mergeTimelineEvents,
  type TimelineTurn,
} from './codingTimeline'
import type { HarnessSurfaceContext } from '../harness/types'

export type { ChatMessage, ToolActivity } from './codingEvents'

const NEW_SESSION_RUNTIME_PROFILE_KEY = 'sage.coding.newRuntimeProfile'

function storedNewSessionRuntimeProfile(): CodingRuntimeProfile | null {
  try {
    const stored = window.localStorage.getItem(NEW_SESSION_RUNTIME_PROFILE_KEY)
    return stored === 'legacy' || stored === 'deerflow_v2' ? stored : null
  } catch {
    return null
  }
}

function persistNewSessionRuntimeProfile(profile: CodingRuntimeProfile) {
  try {
    window.localStorage.setItem(NEW_SESSION_RUNTIME_PROFILE_KEY, profile)
  } catch {
    // Storage can be unavailable in hardened browser contexts; the in-memory choice still works.
  }
}

function clearStoredNewSessionRuntimeProfile() {
  try {
    window.localStorage.removeItem(NEW_SESSION_RUNTIME_PROFILE_KEY)
  } catch {
    // The in-memory selection remains authoritative for this browser session.
  }
}

export type CodingSessionUiState = {
  workspaceRoot: string
  workspaceId: string
  permissionMode: PermissionMode
  runtimeProfile: CodingRuntimeProfile
  timeline: CodingTimelineEvent[]
  olderTimeline: CodingTimelineEvent[]
  turns: TimelineTurn[]
  timelineCursor: number
  olderCursor: number | null
  timelineHasMore: boolean
  timelineInitialized: boolean
  timelineLoading: boolean
  activeRun: CodingActiveRun | null
  connectionState: CodingConnectionState
  stateSequence: number
  currentModelId: string
  reasoningMode: 'off' | 'low' | 'medium' | 'high'
  contextRequestGeneration: number
  memoryRequestGeneration: number
  memoryMutationGeneration: number
  knowledgeSourceProposalRequestGeneration: number
  knowledgeSourceProposalMutationGeneration: Record<string, number>
  knowledgeSourceProposalDetailGeneration: Record<string, number>
  compactionGeneration: number
  modelMutationGeneration: number
  permissionMutationGeneration: number
  scrollAnchor: { eventId: string; offset: number } | null
  messages: ChatMessage[]
  optimisticMessage: ChatMessage | null
  legacyMessages: ChatMessage[]
  legacyTranscript: ChatMessage[]
  legacyLoaded: boolean
  isThinking: boolean
  errorMessage: string
  contextChars: number
  contextSnapshot: CodingContextSnapshot | null
  compactionState: 'idle' | 'running' | 'succeeded' | 'failed'
  compactionError: string
  pendingApproval: CodingApproval | null
  approvalBusy: boolean
  thinkingPhase: string
  runtimeMode: string
  planTopic: string
  planPath: string
  planReview: PlanReviewState | null
  lastDiffInfo: DiffInfo | null
  diffInfoByRun: Record<string, DiffInfo>
  memoryProposals: MemoryProposal[]
  memoryProposalBusy: Record<string, boolean>
  memoryProposalError: string
  memoryProposalRefresh: number
  knowledgeSourceProposals: CodingKnowledgeSourceProposal[]
  knowledgeSourceProposalDetails: Record<string, CodingKnowledgeSourceProposalDetail>
  knowledgeSourceProposalBusy: Record<string, boolean>
  knowledgeSourceProposalDetailBusy: Record<string, boolean>
  knowledgeSourceProposalError: string
  knowledgeSourceProposalRefresh: number
}

function createSessionUiState(): CodingSessionUiState {
  return {
    workspaceRoot: '',
    workspaceId: '',
    permissionMode: 'default',
    runtimeProfile: 'legacy',
    timeline: [],
    olderTimeline: [],
    turns: [],
    timelineCursor: 0,
    olderCursor: null,
    timelineHasMore: false,
    timelineInitialized: false,
    timelineLoading: false,
    activeRun: null,
    connectionState: 'idle',
    stateSequence: 0,
    currentModelId: '',
    reasoningMode: 'off',
    contextRequestGeneration: 0,
    memoryRequestGeneration: 0,
    memoryMutationGeneration: 0,
    knowledgeSourceProposalRequestGeneration: 0,
    knowledgeSourceProposalMutationGeneration: {},
    knowledgeSourceProposalDetailGeneration: {},
    compactionGeneration: 0,
    modelMutationGeneration: 0,
    permissionMutationGeneration: 0,
    scrollAnchor: null,
    messages: [],
    optimisticMessage: null,
    legacyMessages: [],
    legacyTranscript: [],
    legacyLoaded: false,
    isThinking: false,
    errorMessage: '',
    contextChars: 0,
    contextSnapshot: null,
    compactionState: 'idle',
    compactionError: '',
    pendingApproval: null,
    approvalBusy: false,
    thinkingPhase: '',
    runtimeMode: 'default',
    planTopic: '',
    planPath: '',
    planReview: null,
    lastDiffInfo: null,
    diffInfoByRun: {},
    memoryProposals: [],
    memoryProposalBusy: {},
    memoryProposalError: '',
    memoryProposalRefresh: 0,
    knowledgeSourceProposals: [],
    knowledgeSourceProposalDetails: {},
    knowledgeSourceProposalBusy: {},
    knowledgeSourceProposalDetailBusy: {},
    knowledgeSourceProposalError: '',
    knowledgeSourceProposalRefresh: 0,
  }
}

function pendingApprovalFromTimeline(
  timeline: CodingTimelineEvent[],
  sessionId: string,
): CodingApproval | null {
  let pending: (CodingApproval & { run_id?: string }) | null = null
  for (const event of timeline) {
    const type = event.payload.type
    if (event.kind === 'terminal' && pending?.run_id === event.run_id) {
      pending = null
      continue
    }
    if (type === 'approval_required') {
      pending = {
        approval_id: typeof event.payload.approval_id === 'string' ? event.payload.approval_id : '',
        session_id: sessionId,
        tool: typeof event.payload.tool === 'string' ? event.payload.tool : '',
        args: event.payload.args && typeof event.payload.args === 'object'
          ? event.payload.args as Record<string, unknown> : {},
        description: typeof event.payload.description === 'string' ? event.payload.description : '',
        pattern_key: typeof event.payload.pattern_key === 'string' ? event.payload.pattern_key : '',
        run_id: event.run_id,
      }
      continue
    }
    if (!pending) continue
    const eventTool = typeof event.payload.tool === 'string' ? event.payload.tool : ''
    if (
      event.run_id === pending.run_id && eventTool === pending.tool &&
      (type === 'approval_granted' || type === 'tool_result')
    ) {
      pending = null
    }
  }
  return pending
}

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
  const sessionsById = ref<Record<string, CodingSessionUiState>>({})
  const detachedSession = createSessionUiState()
  function ensureSession(targetSessionId: string): CodingSessionUiState {
    if (!targetSessionId) return detachedSession
    if (!sessionsById.value[targetSessionId]) {
      sessionsById.value[targetSessionId] = createSessionUiState()
    }
    return sessionsById.value[targetSessionId]
  }
  function sessionField<K extends keyof CodingSessionUiState>(key: K) {
    return computed<CodingSessionUiState[K]>({
      get: () => ensureSession(sessionId.value)[key],
      set: (value) => { ensureSession(sessionId.value)[key] = value },
    })
  }
  const workspaceRoot = sessionField('workspaceRoot')
  const workspaceId = sessionField('workspaceId')
  const messages = sessionField('messages')
  const optimisticMessage = sessionField('optimisticMessage')
  const legacyMessages = sessionField('legacyMessages')
  const isThinking = sessionField('isThinking')
  const errorMessage = sessionField('errorMessage')
  const currentModelId = sessionField('currentModelId')
  const reasoningMode = sessionField('reasoningMode')
  const contextChars = sessionField('contextChars')
  const contextSnapshot = sessionField('contextSnapshot')
  const compactionState = sessionField('compactionState')
  const compactionError = sessionField('compactionError')
  const pendingApproval = sessionField('pendingApproval')
  const approvalBusy = sessionField('approvalBusy')
  const thinkingPhase = sessionField('thinkingPhase')
  const runtimeMode = sessionField('runtimeMode')
  const permissionMode = sessionField('permissionMode')
  const runtimeProfile = sessionField('runtimeProfile')
  const planTopic = sessionField('planTopic')
  const planPath = sessionField('planPath')
  const planReview = sessionField('planReview')
  const lastDiffInfo = sessionField('lastDiffInfo')
  const diffInfoByRun = sessionField('diffInfoByRun')
  const diffDrawerVisible = ref(false)
  const currentDiffData = ref<CodingRunDiff | null>(null)
  const codingSessions = ref<CodingSessionSummary[]>([])
  const runs = ref<CodingRunSummary[]>([])
  const selectedRun = ref<CodingRunDetailResponse | null>(null)
  const memoryProposals = sessionField('memoryProposals')
  const memoryProposalBusy = sessionField('memoryProposalBusy')
  const memoryProposalError = sessionField('memoryProposalError')
  const memoryProposalRefresh = sessionField('memoryProposalRefresh')
  const knowledgeSourceProposals = sessionField('knowledgeSourceProposals')
  const knowledgeSourceProposalDetails = sessionField('knowledgeSourceProposalDetails')
  const knowledgeSourceProposalBusy = sessionField('knowledgeSourceProposalBusy')
  const knowledgeSourceProposalDetailBusy = sessionField('knowledgeSourceProposalDetailBusy')
  const knowledgeSourceProposalError = sessionField('knowledgeSourceProposalError')
  const knowledgeSourceProposalRefresh = sessionField('knowledgeSourceProposalRefresh')
  const timeline = sessionField('timeline')
  const olderTimeline = sessionField('olderTimeline')
  const visibleTimeline = computed(() => mergeTimelineEvents(olderTimeline.value, timeline.value))
  const turns = sessionField('turns')
  const timelineCursor = sessionField('timelineCursor')
  const olderCursor = sessionField('olderCursor')
  const timelineHasMore = sessionField('timelineHasMore')
  const timelineInitialized = sessionField('timelineInitialized')
  const timelineLoading = sessionField('timelineLoading')
  const activeRun = sessionField('activeRun')
  const connectionState = sessionField('connectionState')
  const scrollAnchor = sessionField('scrollAnchor')

  const skills = ref<CodingSkillSummary[]>([])
  const mcpServers = ref<CodingMcpServer[]>([])
  const models = ref<CodingModel[]>([])
  const initialRuntimeProfilePreference = storedNewSessionRuntimeProfile()
  const availableRuntimeProfiles = ref<CodingRuntimeProfile[]>(['legacy'])
  const newSessionRuntimeProfile = ref<CodingRuntimeProfile>(
    initialRuntimeProfilePreference ?? 'legacy',
  )
  const providerSettings = ref<CodingProviderSettings | null>(null)
  const accountProviders = ref<CloudModelProvider[]>([])
  const accountDefaultModel = ref<string | null>(null)
  const accountProviderAuthenticated = ref(false)
  const accountProviderLoading = ref(false)
  const accountProviderError = ref('')
  const usageSummary = ref<CodingUsageSummary | null>(null)
  const usageRange = ref<'7d' | '30d' | '90d' | '365d'>('30d')
  const providerSettingsError = ref('')
  const usageError = ref('')
  let usageRequestGeneration = 0
  let modelRequestGeneration = 0
  let modelCatalogBootstrapped = false
  let modelCatalogPromise: Promise<void> | null = null
  let hasNewSessionRuntimePreference = initialRuntimeProfilePreference !== null
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
  const pendingInitialPrompts = new Map<string, {
    content: string
    surfaceContext?: HarnessSurfaceContext | null
  }>()
  let approvalPollTimer: number | null = null
  let fileTreeGeneration = 0
  let runsRequestGeneration = 0
  let sessionsRequestGeneration = 0
  let gitRequestGeneration = 0
  let runDetailGeneration = 0
  let runDiffGeneration = 0
  let filePreviewGeneration = 0
  let selectionGeneration = 0
  const dirCache = new Map<string, CodingFileEntry[]>()
  const liveRefreshPending = new Set<string>()
  const memoryRefreshPending = new Set<string>()
  const sourceProposalRefreshPending = new Set<string>()
  const terminalRefreshPending = new Set<string>()
  const scheduledTimeouts = new Set<number>()
  let approvalPollSessionId = ''

  const MAX_RESIDENT_TIMELINE_EVENTS = 2_000
  const MAX_RESIDENT_OLDER_EVENTS = 10_000

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

  function mergeTimelinePage(
    targetSessionId: string,
    items: CodingTimelineEvent[],
    page: {
      next_cursor: number
      has_more?: boolean
      active_run?: CodingActiveRun | null
      older_cursor?: number | null
      latest_cursor?: number
    },
    source: 'history' | 'older' | 'live' | 'replay' = 'history',
  ) {
    const state = ensureSession(targetSessionId)
    const validItems = items.filter((item) => item.session_id === targetSessionId)
    const known = new Set([...state.olderTimeline, ...state.timeline].map((item) => item.event_id))
    const fresh = validItems.filter((item) => !known.has(item.event_id))
    if (state.optimisticMessage && (source === 'live' || source === 'replay') && fresh.some((item) => {
      const content = typeof item.payload.content === 'string' ? item.payload.content : ''
      return (item.kind === 'user' || item.payload.type === 'user') &&
        content === state.optimisticMessage?.content
    })) {
      state.optimisticMessage = null
    }
    if (source === 'older') {
      state.olderTimeline = mergeTimelineEvents(state.olderTimeline, validItems)
        .slice(-MAX_RESIDENT_OLDER_EVENTS)
    } else {
      state.timeline = mergeTimelineEvents(state.timeline, validItems)
        .slice(-MAX_RESIDENT_TIMELINE_EVENTS)
    }
    if (source !== 'older') {
      state.timelineCursor = Math.max(
        state.timelineCursor,
        source === 'history' ? page.latest_cursor ?? 0 : 0,
        page.next_cursor,
        ...validItems.map((item) => item.sequence),
      )
    }
    if (Object.prototype.hasOwnProperty.call(page, 'older_cursor')) {
      state.olderCursor = page.older_cursor ?? null
      state.timelineHasMore = state.olderCursor !== null
    } else if (page.has_more !== undefined && source !== 'live') {
      state.timelineHasMore = page.has_more
    }
    if (source === 'older' && state.olderTimeline.length >= MAX_RESIDENT_OLDER_EVENTS) {
      state.olderCursor = null
      state.timelineHasMore = false
    }
    if (source !== 'older' && Object.prototype.hasOwnProperty.call(page, 'active_run')) {
      state.activeRun = page.active_run ?? null
    }
    for (const item of fresh
      .filter((item) => source !== 'older' && item.sequence > state.stateSequence)
      .sort((left, right) => left.sequence - right.sequence)) {
      const effect = applyTimelineState(targetSessionId, state, item)
      state.stateSequence = item.sequence
      if (source === 'live') applyLiveSideEffects(targetSessionId, item, effect)
    }
    rebuildTimelineProjection(state)
    if (state.legacyLoaded && state.legacyTranscript.length > 0) {
      const projected = state.messages.slice(state.legacyMessages.length)
      const prefix = legacyPrefix(state.legacyTranscript, projected)
      if (prefix.length !== state.legacyMessages.length) {
        state.legacyMessages = prefix
        rebuildTimelineProjection(state)
      }
    }
  }

  function handleTimelineEvent(targetSessionId: string, event: CodingTimelineEvent) {
    if (event.session_id !== targetSessionId) return
    mergeTimelinePage(targetSessionId, [event], {
      next_cursor: event.sequence,
      latest_cursor: event.sequence,
    }, 'live')
  }

  function applyTimelineState(
    targetSessionId: string,
    state: CodingSessionUiState,
    event: CodingTimelineEvent,
  ) {
    const payloadType = typeof event.payload.type === 'string' ? event.payload.type : ''
    state.errorMessage = ''
    if (event.payload.event === 'run_started') {
      state.activeRun = { run_id: event.run_id, status: 'running' }
      state.isThinking = true
    }
    if (event.kind === 'terminal') {
      if (!state.activeRun || state.activeRun.run_id === event.run_id) {
        if (state.activeRun?.run_id === event.run_id) state.activeRun = null
        state.isThinking = false
        state.thinkingPhase = ''
      }
    }
    if (payloadType) {
      const effect = applyCodingEvent(
        {
          sessionId: ref(targetSessionId),
          messages: toRef(state, 'messages'),
          isThinking: toRef(state, 'isThinking'),
          errorMessage: toRef(state, 'errorMessage'),
          contextChars: toRef(state, 'contextChars'),
          contextSnapshot: toRef(state, 'contextSnapshot'),
          compactionState: toRef(state, 'compactionState'),
          compactionError: toRef(state, 'compactionError'),
          pendingApproval: toRef(state, 'pendingApproval'),
          thinkingPhase: toRef(state, 'thinkingPhase'),
          runtimeMode: toRef(state, 'runtimeMode'),
          planTopic: toRef(state, 'planTopic'),
          planPath: toRef(state, 'planPath'),
          planReview: toRef(state, 'planReview'),
          lastDiffInfo: toRef(state, 'lastDiffInfo'),
          diffInfoByRun: toRef(state, 'diffInfoByRun'),
          memoryProposals: toRef(state, 'memoryProposals'),
          memoryProposalRefresh: toRef(state, 'memoryProposalRefresh'),
          knowledgeSourceProposalRefresh: toRef(state, 'knowledgeSourceProposalRefresh'),
        },
        event.payload as unknown as CodingServerEvent,
      )
      if (state.activeRun?.run_id === event.run_id && event.kind !== 'terminal') {
        state.isThinking = true
      }
      return effect
    }
    if (state.activeRun?.run_id === event.run_id && event.kind !== 'terminal') {
      state.isThinking = true
    }
    return {}
  }

  function applyLiveSideEffects(
    targetSessionId: string,
    event: CodingTimelineEvent,
    effect: ReturnType<typeof applyCodingEvent>,
  ) {
    const payloadType = typeof event.payload.type === 'string' ? event.payload.type : ''
    if (effect.approvalRequired && targetSessionId === sessionId.value) {
      void enrichApprovalPreview(targetSessionId, ensureSession(targetSessionId))
      startApprovalPolling(targetSessionId)
    }
    if (effect.memoryProposalReady) scheduleMemoryRefresh(targetSessionId)
    if (effect.knowledgeSourceProposalReady) scheduleSourceProposalRefresh(targetSessionId)
    if (effect.toolResult && !effect.toolResult.is_error &&
      ['write_file', 'patch_file', 'run_shell'].includes(effect.toolResult.tool)) {
      scheduleWorkspaceRefresh(targetSessionId)
    }
    if (payloadType === 'turn_finished') scheduleContextRefresh(targetSessionId)
    if (event.kind === 'terminal') {
      const pending = pendingApprovalFromTimeline(
        mergeTimelineEvents(ensureSession(targetSessionId).olderTimeline, ensureSession(targetSessionId).timeline),
        targetSessionId,
      ) as (CodingApproval & { run_id?: string }) | null
      if (!pending || pending.run_id === event.run_id) stopApprovalPolling(targetSessionId)
      scheduleTerminalRefresh(targetSessionId)
    }
  }

  function schedule(callback: () => void) {
    const timer = window.setTimeout(() => {
      scheduledTimeouts.delete(timer)
      callback()
    }, 0)
    scheduledTimeouts.add(timer)
  }

  function scheduleWorkspaceRefresh(targetSessionId: string) {
    if (liveRefreshPending.has(targetSessionId)) return
    liveRefreshPending.add(targetSessionId)
    schedule(() => {
      liveRefreshPending.delete(targetSessionId)
      if (targetSessionId === sessionId.value) void refreshWorkspaceView()
    })
  }

  function scheduleMemoryRefresh(targetSessionId: string) {
    if (memoryRefreshPending.has(targetSessionId)) return
    memoryRefreshPending.add(targetSessionId)
    schedule(() => {
      memoryRefreshPending.delete(targetSessionId)
      void loadMemoryProposals(targetSessionId)
    })
  }

  function scheduleSourceProposalRefresh(targetSessionId: string) {
    if (sourceProposalRefreshPending.has(targetSessionId)) return
    sourceProposalRefreshPending.add(targetSessionId)
    schedule(() => {
      sourceProposalRefreshPending.delete(targetSessionId)
      void loadKnowledgeSourceProposals(targetSessionId)
    })
  }

  function scheduleContextRefresh(targetSessionId: string) {
    schedule(() => {
      void loadContext(targetSessionId)
    })
  }

  function scheduleTerminalRefresh(targetSessionId: string) {
    if (terminalRefreshPending.has(targetSessionId)) return
    terminalRefreshPending.add(targetSessionId)
    schedule(() => {
      terminalRefreshPending.delete(targetSessionId)
      if (targetSessionId !== sessionId.value) return
      void loadSessions().catch(() => {})
      void loadRuns(targetSessionId)
    })
  }

  function rebuildTimelineProjection(state: CodingSessionUiState) {
    const projectedTimeline = mergeTimelineEvents(state.olderTimeline, state.timeline)
    const projection = createTimelineProjection(projectedTimeline)
    state.turns = projection.turns
    const projectedMessages = projection.turns.flatMap((turn): ChatMessage[] => {
      const result: ChatMessage[] = []
      if (turn.user) {
        result.push({
          id: `${turn.id}:user`,
          run_id: turn.run_id,
          role: 'user',
          content: turn.user.content,
        })
      }
      if (turn.assistant || turn.tools.length > 0 || turn.model.length > 0) {
        result.push({
          id: `${turn.id}:assistant`,
          run_id: turn.run_id,
          role: 'assistant',
          content: turn.assistant?.content ?? '',
          tools: turn.tools.map((tool) => ({
            tool: tool.tool,
            args: tool.args,
            status: tool.status === 'running' || tool.status === 'blocked'
              ? 'running'
              : tool.is_error || tool.status === 'error' ? 'error' : 'done',
            content: tool.result,
            retrieval: tool.retrieval,
          })),
          activities: turn.model.map((item) => ({
            kind: 'model' as const,
            label: item.type === 'model_requested' ? '请求模型响应' : '处理模型响应',
            status: item.status === 'running' ? 'running' as const
              : item.status === 'error' ? 'error' as const : 'done' as const,
          })),
          isThinking: state.activeRun?.run_id === turn.run_id && !turn.terminal,
        })
      }
      return result
    })
    // When timeline is empty (backend hasn't implemented timeline contract yet),
    // preserve existing messages built by applyCodingEvent (which contain
    // tools/activities from raw WebSocket events). Only overwrite when we have
    // actual timeline projection data.
    if (projectedMessages.length > 0) {
      state.messages = [...state.legacyMessages, ...projectedMessages]
    } else {
      // Timeline unavailable: preserve raw-event messages (built by
      // applyCodingEvent) without duplicating legacyMessages prefix.
      const rawMessages = state.messages.slice(state.legacyMessages.length)
      state.messages = [...state.legacyMessages, ...rawMessages]
    }
    const targetSessionId = projectedTimeline[0]?.session_id ?? ''
    state.pendingApproval = pendingApprovalFromTimeline(projectedTimeline, targetSessionId)
    state.isThinking = state.activeRun !== null
  }

  async function loadTimeline(targetSessionId = sessionId.value) {
    if (!targetSessionId) return
    const state = ensureSession(targetSessionId)
    if (state.timelineLoading) return
    state.timelineLoading = true
    try {
      if (state.timelineInitialized) {
        let after = state.timelineCursor
        while (true) {
          const response = await fetchCodingTimeline(targetSessionId, after, 100)
          mergeTimelinePage(targetSessionId, response.items, response, 'replay')
          if (!response.has_more) break
          if (response.next_cursor <= after) {
            throw new Error('timeline replay cursor did not advance')
          }
          after = response.next_cursor
        }
      } else {
        const response = await fetchCodingTimelineTail(targetSessionId, 100)
        mergeTimelinePage(targetSessionId, response.items, response, 'history')
      }
      state.timelineInitialized = true
      state.errorMessage = ''
      if (!state.legacyLoaded) {
        const legacy = await loadSessionMessages(targetSessionId)
        const projected = state.messages.slice(state.legacyMessages.length)
        state.legacyTranscript = legacy
        state.legacyMessages = legacyPrefix(legacy, projected)
        state.legacyLoaded = true
        rebuildTimelineProjection(state)
      }
    } catch (error) {
      state.errorMessage = error instanceof Error ? error.message : String(error)
      if (!state.timelineInitialized && state.timeline.length === 0) {
        state.legacyTranscript = await loadSessionMessages(targetSessionId)
        state.legacyMessages = state.legacyTranscript
        state.legacyLoaded = true
        rebuildTimelineProjection(state)
      }
    } finally {
      state.timelineLoading = false
    }
  }

  async function loadOlderTimeline(targetSessionId = sessionId.value) {
    const state = ensureSession(targetSessionId)
    if (state.olderCursor === null || state.timelineLoading) return
    const before = state.olderCursor
    state.timelineLoading = true
    try {
      const response = await fetchOlderCodingTimeline(targetSessionId, before, 100)
      mergeTimelinePage(targetSessionId, response.items, response, 'older')
      state.errorMessage = ''
    } catch (error) {
      state.errorMessage = error instanceof Error ? error.message : String(error)
    } finally {
      state.timelineLoading = false
    }
  }

  const loadMoreTimeline = loadOlderTimeline

  function legacyPrefix(legacy: ChatMessage[], projected: ChatMessage[]): ChatMessage[] {
    const max = Math.min(legacy.length, projected.length)
    for (let count = max; count > 0; count -= 1) {
      const left = legacy.slice(-count)
      const right = projected.slice(0, count)
      if (left.every((item, index) => sameLegacyMessage(item, right[index]))) {
        return legacy.slice(0, -count)
      }
    }
    return legacy
  }

  function sameLegacyMessage(left: ChatMessage, right: ChatMessage): boolean {
    if (left.role !== right.role) return false
    if (left.content === right.content) return true
    return left.role === 'assistant' && Boolean(right.content) && left.content.startsWith(right.content)
  }

  function setScrollAnchor(eventId: string, offset: number) {
    scrollAnchor.value = eventId ? { eventId, offset } : null
  }

  async function initialize() {
    if (sessionId.value) return
    const selection = ++selectionGeneration
    await bootstrapModelCatalog()
    if (selection !== selectionGeneration) return
    const session = await startCodingSession(
      undefined,
      'ask',
      hasNewSessionRuntimePreference ? newSessionRuntimeProfile.value : undefined,
    )
    if (selection !== selectionGeneration) return
    sessionId.value = session.session_id
    ensureSession(session.session_id)
    workspaceRoot.value = session.workspace_root
    workspaceId.value = session.workspace_id
    permissionMode.value = session.permission_mode
    runtimeProfile.value = session.runtime_profile || 'legacy'
    await Promise.all([
      loadSkills(),
      loadMcpServers(),
      loadModels(),
      loadTimeline(session.session_id),
      loadContext(),
      loadGitStatus(),
      loadFiles('.'),
      loadSessions().catch(() => {}),
      loadRuns(),
    ])
    if (selection !== selectionGeneration || sessionId.value !== session.session_id) return
    connectSocket()
  }

  function connectSocket() {
    if (!sessionId.value) return
    const targetSessionId = sessionId.value
    const state = ensureSession(targetSessionId)
    void loadMemoryProposals()
    void loadKnowledgeSourceProposals()
    stream?.disconnect()
    stream = new CodingStream({
      onEvent: handleTimelineEvent,
      onRawEvent: handleServerEvent,
      onOpen: (openedSessionId) => {
        if (openedSessionId !== sessionId.value) return
        const prompt = pendingInitialPrompts.get(openedSessionId)
        if (prompt && sendMessage(prompt.content, prompt.surfaceContext)) {
          pendingInitialPrompts.delete(openedSessionId)
        }
      },
      onConnectionState: (openedSessionId, state) => {
        ensureSession(openedSessionId).connectionState = state
      },
      onError: (message) => {
        ensureSession(targetSessionId).errorMessage = message
      },
    })
    stream.connect(
      targetSessionId,
      buildCodingStreamUrl(targetSessionId, state.timelineCursor),
    )
  }

  function stopSessionTransport() {
    stopApprovalPolling()
    stream?.disconnect()
    stream = null
  }

  function handleServerEvent(event: CodingServerEvent) {
    const targetSessionId = sessionId.value
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
        diffInfoByRun,
        memoryProposals,
        memoryProposalRefresh,
        knowledgeSourceProposalRefresh,
      },
      event,
    )
    if (effect.memoryProposalReady) void loadMemoryProposals()
    if (effect.knowledgeSourceProposalReady) void loadKnowledgeSourceProposals()
    if (event.type === 'turn_finished') {
      schedule(() => void loadContext(targetSessionId))
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
        void loadSessions().catch(() => {})
        void loadRuns()
      }
    }
  }

  function sendMessage(
    content: string,
    surfaceContext?: HarnessSurfaceContext | null,
  ): boolean {
    if (!content.trim()) return false
    if (!stream || connectionState.value !== 'connected') {
      errorMessage.value = '连接正在恢复，消息已保留，请稍后重试'
      if (!stream || connectionState.value === 'disconnected') connectSocket()
      return false
    }
    const sent = stream.send(content, surfaceContext)
    if (!sent) return false
    const optimistic = {
      id: `optimistic:${sessionId.value}:${Date.now()}`,
      role: 'user' as const,
      content,
    }
    optimisticMessage.value = optimistic
    messages.value.push(optimistic)
    isThinking.value = true
    errorMessage.value = ''
    pendingApproval.value = null
    thinkingPhase.value = '正在理解任务'
    startApprovalPolling()
    return true
  }

  async function pollApproval(targetSessionId: string, state: CodingSessionUiState) {
    if (!targetSessionId || (!state.pendingApproval && !state.isThinking && !state.activeRun)) return
    try {
      const approval = await fetchCodingApprovalPending(targetSessionId)
      if (approvalPollSessionId !== targetSessionId) return
      state.pendingApproval = approval
    } catch {
      // WebSocket approval_required is primary; polling is a resilience layer.
    }
  }

  function startApprovalPolling(targetSessionId = sessionId.value) {
    if (!targetSessionId) return
    if (approvalPollTimer !== null && approvalPollSessionId === targetSessionId) return
    stopApprovalPolling()
    approvalPollSessionId = targetSessionId
    const state = ensureSession(targetSessionId)
    approvalPollTimer = window.setInterval(() => {
      void pollApproval(targetSessionId, state)
    }, 1500)
  }

  function stopApprovalPolling(targetSessionId?: string) {
    if (targetSessionId && approvalPollSessionId !== targetSessionId) return
    if (approvalPollTimer === null) return
    window.clearInterval(approvalPollTimer)
    approvalPollTimer = null
    approvalPollSessionId = ''
  }

  async function enrichApprovalPreview(
    targetSessionId = sessionId.value,
    state = ensureSession(targetSessionId),
  ) {
    const approval = state.pendingApproval
    if (!approval) return
    const path = typeof approval.args.path === 'string' ? approval.args.path : ''
    if (!path) return
    if (approval.tool === 'patch_file') {
      const oldText = typeof approval.args.old_text === 'string' ? approval.args.old_text : ''
      const newText = typeof approval.args.new_text === 'string' ? approval.args.new_text : ''
      state.pendingApproval = {
        ...approval,
        diff_preview: buildSimpleDiff(oldText, newText),
      }
      return
    }
    if (approval.tool !== 'write_file') return
    const content = typeof approval.args.content === 'string' ? approval.args.content : ''
    const fileExists = await workspaceFileExists(targetSessionId, path)
    if (!fileExists) {
      if (state.pendingApproval?.approval_id !== approval.approval_id) return
      state.pendingApproval = {
        ...state.pendingApproval,
        diff_preview: buildSimpleDiff('', content),
      }
      return
    }
    try {
      const current = await fetchCodingFile(targetSessionId, path)
      if (state.pendingApproval?.approval_id !== approval.approval_id) return
      state.pendingApproval = {
        ...state.pendingApproval,
        diff_preview: buildSimpleDiff(current.content, content),
      }
    } catch {
      if (state.pendingApproval?.approval_id !== approval.approval_id) return
      state.pendingApproval = {
        ...approval,
        diff_preview: buildSimpleDiff('', content),
      }
    }
  }

  async function workspaceFileExists(targetSessionId: string, path: string): Promise<boolean> {
    if (!targetSessionId) return false
    const segments = path.replace(/^\.\//, '').split('/')
    const name = segments.pop()
    if (!name) return false
    const parent = segments.join('/') || '.'
    try {
      const listing = await fetchCodingFiles(targetSessionId, parent)
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
    const targetSessionId = sessionId.value
    if (!targetSessionId) return
    const state = ensureSession(targetSessionId)
    if (!state.pendingApproval || state.approvalBusy) return
    const approvalId = state.pendingApproval.approval_id
    state.approvalBusy = true
    try {
      await respondCodingApproval(targetSessionId, approvalId, choice)
      if (state.pendingApproval?.approval_id === approvalId) state.pendingApproval = null
    } catch (error) {
      state.errorMessage = error instanceof Error ? error.message : String(error)
    } finally {
      state.approvalBusy = false
    }
  }

  async function stopCurrentRun() {
    const targetSessionId = sessionId.value
    if (!targetSessionId) return
    const state = ensureSession(targetSessionId)
    const runId = state.activeRun?.run_id
    if (!runId) return
    await stopCodingRun(targetSessionId, runId)
    state.pendingApproval = null
  }

  async function approvePlan() {
    const targetSessionId = sessionId.value
    if (!targetSessionId) return
    const state = ensureSession(targetSessionId)
    if (!state.planReview) return
    try {
      const result = await approveCodingPlan(targetSessionId)
      // The REST call runs outside run_turn, so runtime_mode_changed is not
      // pushed over WebSocket. Apply the response locally.
      if (result.mode === 'default') {
        state.runtimeMode = 'default'
        state.planTopic = ''
        state.planPath = ''
        state.planReview = null
      }
    } catch (e) {
      state.errorMessage = String(e)
    }
  }

  async function rejectPlan() {
    const targetSessionId = sessionId.value
    if (!targetSessionId) return
    const state = ensureSession(targetSessionId)
    if (!state.planReview) return
    try {
      await rejectCodingPlan(targetSessionId)
      // Backend stays in plan mode (no runtime_mode_changed), so clear locally.
      state.planReview = null
    } catch (e) {
      state.errorMessage = String(e)
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

  async function loadModels(targetSessionId: string | null = sessionId.value || null) {
    const generation = ++modelRequestGeneration
    try {
      const res = await fetchCodingModels(targetSessionId || undefined)
      if (generation !== modelRequestGeneration) return false
      models.value = res.models
      reconcileRuntimeProfiles(res.runtime_profiles, res.default_runtime_profile)
      if (res.current) currentModelId.value = res.current
      else if (res.models.length > 0) currentModelId.value = res.models[0].id
      reasoningMode.value = res.reasoning_mode || 'off'
      return true
    } catch {
      if (generation === modelRequestGeneration) models.value = []
      return false
    }
  }

  function reconcileRuntimeProfiles(profiles: unknown, serverDefault: unknown) {
    const advertised = Array.isArray(profiles)
      ? profiles.filter((profile): profile is CodingRuntimeProfile =>
          profile === 'legacy' || profile === 'deerflow_v2')
      : []
    availableRuntimeProfiles.value = [
      'legacy',
      ...(advertised.includes('deerflow_v2') ? ['deerflow_v2' as const] : []),
    ]
    const effectiveServerDefault = (
      (serverDefault === 'legacy' || serverDefault === 'deerflow_v2')
      && availableRuntimeProfiles.value.includes(serverDefault)
    ) ? serverDefault : 'legacy'
    const storedPreference = storedNewSessionRuntimeProfile()
    if (storedPreference && availableRuntimeProfiles.value.includes(storedPreference)) {
      hasNewSessionRuntimePreference = true
      newSessionRuntimeProfile.value = storedPreference
      return
    }
    if (storedPreference) {
      clearStoredNewSessionRuntimeProfile()
      hasNewSessionRuntimePreference = false
    }
    if (
      hasNewSessionRuntimePreference
      && availableRuntimeProfiles.value.includes(newSessionRuntimeProfile.value)
    ) {
      return
    }
    hasNewSessionRuntimePreference = false
    newSessionRuntimeProfile.value = effectiveServerDefault
  }

  function setNewSessionRuntimeProfile(profile: CodingRuntimeProfile): boolean {
    if (!availableRuntimeProfiles.value.includes(profile)) return false
    newSessionRuntimeProfile.value = profile
    hasNewSessionRuntimePreference = true
    persistNewSessionRuntimeProfile(profile)
    return true
  }

  async function bootstrapModelCatalog(force = false) {
    if (modelCatalogBootstrapped && !force) return
    if (modelCatalogPromise) {
      await modelCatalogPromise
      if (!force) return
    }
    if (modelCatalogPromise) return modelCatalogPromise
    modelCatalogPromise = (async () => {
      const loaded = await loadModels(null)
      modelCatalogBootstrapped = loaded
    })()
    try {
      await modelCatalogPromise
    } finally {
      modelCatalogPromise = null
    }
  }

  async function loadProviderSettings() {
    try {
      providerSettings.value = await fetchCodingProviderSettings()
      providerSettingsError.value = ''
    } catch (error) {
      providerSettings.value = null
      providerSettingsError.value = error instanceof Error ? error.message : String(error)
    }
  }

  async function loadModelProviderSettings() {
    accountProviderLoading.value = true
    accountProviderError.value = ''
    try {
      const account = await fetchCloudModelProviders()
      if (account === null) {
        accountProviders.value = []
        accountDefaultModel.value = null
        accountProviderAuthenticated.value = false
        await loadProviderSettings()
        return false
      }
      accountProviders.value = account.providers
      accountDefaultModel.value = account.default_model
      accountProviderAuthenticated.value = true
      providerSettingsError.value = ''
      return true
    } catch (error) {
      accountProviders.value = []
      accountDefaultModel.value = null
      accountProviderAuthenticated.value = false
      accountProviderError.value = error instanceof Error ? error.message : String(error)
      return false
    } finally {
      accountProviderLoading.value = false
    }
  }

  async function loadUsage(range: '7d' | '30d' | '90d' | '365d' = usageRange.value) {
    const generation = ++usageRequestGeneration
    usageRange.value = range
    usageSummary.value = null
    usageError.value = ''
    try {
      const summary = await fetchCodingUsage(range)
      if (generation !== usageRequestGeneration) return
      usageSummary.value = summary
      usageError.value = ''
    } catch (error) {
      if (generation !== usageRequestGeneration) return
      usageSummary.value = null
      usageError.value = error instanceof Error ? error.message : String(error)
    }
  }

  async function saveProviderSettings(settings: CodingProviderSettingsUpdate) {
    try {
      providerSettings.value = await saveCodingProviderSettings(settings)
      providerSettingsError.value = ''
      await loadModels()
      return true
    } catch (error) {
      providerSettingsError.value = error instanceof Error ? error.message : String(error)
      return false
    }
  }

  async function loadContext(targetSessionId = sessionId.value) {
    if (!targetSessionId) return
    const state = ensureSession(targetSessionId)
    const requestGeneration = ++state.contextRequestGeneration
    try {
      const snapshot = await fetchCodingContext(targetSessionId)
      if (requestGeneration !== state.contextRequestGeneration) return
      state.contextSnapshot = snapshot
      state.contextChars = snapshot.used_tokens ?? 0
      if (snapshot.model_id) state.currentModelId = snapshot.model_id
    } catch {
      if (requestGeneration === state.contextRequestGeneration) state.contextSnapshot = null
    }
  }

  async function loadRuns(targetSessionId = sessionId.value) {
    if (!targetSessionId) return
    const generation = ++runsRequestGeneration
    try {
      const res = await fetchCodingRuns(targetSessionId)
      if (targetSessionId !== sessionId.value || generation !== runsRequestGeneration) return
      runs.value = res.runs
    } catch {
      if (targetSessionId !== sessionId.value || generation !== runsRequestGeneration) return
      runs.value = []
    }
  }

  async function loadMemoryProposals(targetSessionId = sessionId.value) {
    if (!targetSessionId) return
    const state = ensureSession(targetSessionId)
    const generation = ++state.memoryRequestGeneration
    try {
      const response = await fetchMemoryProposals(targetSessionId)
      if (generation !== state.memoryRequestGeneration) return
      state.memoryProposals = response.proposals.filter((proposal) => proposal.status === 'pending')
      state.memoryProposalError = ''
    } catch (error) {
      if (generation === state.memoryRequestGeneration) {
        state.memoryProposalError = error instanceof Error ? error.message : String(error)
      }
    }
  }

  async function transitionMemoryProposal(
    proposalId: string,
    expectedRevision: number,
    action: 'approve' | 'reject',
  ) {
    if (!sessionId.value) return
    const targetSessionId = sessionId.value
    const state = ensureSession(targetSessionId)
    if (state.memoryProposalBusy[proposalId]) return
    const generation = ++state.memoryMutationGeneration
    state.memoryProposalBusy = { ...state.memoryProposalBusy, [proposalId]: true }
    state.memoryProposalError = ''
    try {
      if (action === 'approve') {
        await approveMemoryProposal(targetSessionId, proposalId, expectedRevision)
      } else {
        await rejectMemoryProposal(targetSessionId, proposalId, expectedRevision)
      }
      if (generation === state.memoryMutationGeneration) {
        // Invalidate any in-flight list response captured before this CAS
        // transition, otherwise it could resurrect the now-terminal proposal.
        state.memoryRequestGeneration += 1
        state.memoryProposals = state.memoryProposals.filter((item) => item.proposal_id !== proposalId)
      }
    } catch (error) {
      if (generation === state.memoryMutationGeneration) {
        state.memoryProposalError = error instanceof Error ? error.message : String(error)
      }
    } finally {
      if (generation !== state.memoryMutationGeneration) return
      const next = { ...state.memoryProposalBusy }
      delete next[proposalId]
      state.memoryProposalBusy = next
    }
  }

  function approveMemoryProposalAction(proposalId: string, expectedRevision: number) {
    return transitionMemoryProposal(proposalId, expectedRevision, 'approve')
  }

  function rejectMemoryProposalAction(proposalId: string, expectedRevision: number) {
    return transitionMemoryProposal(proposalId, expectedRevision, 'reject')
  }

  async function loadKnowledgeSourceProposals(targetSessionId = sessionId.value) {
    if (!targetSessionId) return
    const state = ensureSession(targetSessionId)
    const generation = ++state.knowledgeSourceProposalRequestGeneration
    try {
      const response = await fetchKnowledgeSourceProposals(targetSessionId, null)
      if (generation !== state.knowledgeSourceProposalRequestGeneration) return
      state.knowledgeSourceProposals = response.proposals
        .filter((proposal) => proposal.status === 'pending' || proposal.status === 'applying')
        .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))
      state.knowledgeSourceProposalError = ''
    } catch (error) {
      if (generation === state.knowledgeSourceProposalRequestGeneration) {
        state.knowledgeSourceProposalError = error instanceof Error ? error.message : String(error)
      }
    }
  }

  async function loadKnowledgeSourceProposalDetail(
    proposalId: string,
    targetSessionId = sessionId.value,
  ) {
    if (!targetSessionId || !proposalId) return
    const state = ensureSession(targetSessionId)
    const generation = (state.knowledgeSourceProposalDetailGeneration[proposalId] ?? 0) + 1
    state.knowledgeSourceProposalDetailGeneration = {
      ...state.knowledgeSourceProposalDetailGeneration,
      [proposalId]: generation,
    }
    state.knowledgeSourceProposalDetailBusy = {
      ...state.knowledgeSourceProposalDetailBusy,
      [proposalId]: true,
    }
    try {
      const detail = await fetchKnowledgeSourceProposal(targetSessionId, proposalId)
      if (state.knowledgeSourceProposalDetailGeneration[proposalId] !== generation) return
      state.knowledgeSourceProposalDetails = {
        ...state.knowledgeSourceProposalDetails,
        [proposalId]: detail,
      }
      state.knowledgeSourceProposalError = ''
    } catch (error) {
      if (state.knowledgeSourceProposalDetailGeneration[proposalId] === generation) {
        state.knowledgeSourceProposalError = error instanceof Error ? error.message : String(error)
      }
    } finally {
      if (state.knowledgeSourceProposalDetailGeneration[proposalId] !== generation) return
      const next = { ...state.knowledgeSourceProposalDetailBusy }
      delete next[proposalId]
      state.knowledgeSourceProposalDetailBusy = next
    }
  }

  async function transitionKnowledgeSourceProposal(
    proposalId: string,
    expectedRevision: number,
    action: 'approve' | 'reject',
  ) {
    if (!sessionId.value || !proposalId) return
    const targetSessionId = sessionId.value
    const state = ensureSession(targetSessionId)
    if (state.knowledgeSourceProposalBusy[proposalId]) return
    const generation = (state.knowledgeSourceProposalMutationGeneration[proposalId] ?? 0) + 1
    state.knowledgeSourceProposalMutationGeneration = {
      ...state.knowledgeSourceProposalMutationGeneration,
      [proposalId]: generation,
    }
    state.knowledgeSourceProposalBusy = {
      ...state.knowledgeSourceProposalBusy,
      [proposalId]: true,
    }
    state.knowledgeSourceProposalError = ''
    try {
      let transitioned: CodingKnowledgeSourceProposal
      if (action === 'approve') {
        transitioned = await approveKnowledgeSourceProposalRequest(
          targetSessionId, proposalId, expectedRevision,
        )
      } else {
        transitioned = await rejectKnowledgeSourceProposalRequest(
          targetSessionId, proposalId, expectedRevision,
        )
      }
      if (state.knowledgeSourceProposalMutationGeneration[proposalId] !== generation) return
      state.knowledgeSourceProposalRequestGeneration += 1
      if (transitioned.status === 'pending' || transitioned.status === 'applying') {
        state.knowledgeSourceProposals = state.knowledgeSourceProposals.map(
          (proposal) => proposal.proposal_id === proposalId ? transitioned : proposal,
        )
      } else {
        state.knowledgeSourceProposals = state.knowledgeSourceProposals.filter(
          (proposal) => proposal.proposal_id !== proposalId,
        )
      }
      const details = { ...state.knowledgeSourceProposalDetails }
      delete details[proposalId]
      state.knowledgeSourceProposalDetails = details
    } catch (error) {
      if (state.knowledgeSourceProposalMutationGeneration[proposalId] === generation) {
        state.knowledgeSourceProposalError = error instanceof Error ? error.message : String(error)
      }
    } finally {
      if (state.knowledgeSourceProposalMutationGeneration[proposalId] !== generation) return
      const next = { ...state.knowledgeSourceProposalBusy }
      delete next[proposalId]
      state.knowledgeSourceProposalBusy = next
    }
  }

  function approveKnowledgeSourceProposalAction(proposalId: string, expectedRevision: number) {
    return transitionKnowledgeSourceProposal(proposalId, expectedRevision, 'approve')
  }

  function rejectKnowledgeSourceProposalAction(proposalId: string, expectedRevision: number) {
    return transitionKnowledgeSourceProposal(proposalId, expectedRevision, 'reject')
  }

  async function loadSessions() {
    const generation = ++sessionsRequestGeneration
    try {
      const res = await fetchCodingSessions()
      if (generation !== sessionsRequestGeneration) return
      codingSessions.value = res.sessions
    } catch (error) {
      if (generation !== sessionsRequestGeneration) return
      throw error
    }
  }

  async function updateSessionMetadata(
    targetSessionId: string,
    metadata: { title?: string; pinned?: boolean; archived?: boolean },
  ) {
    const summary = await updateCodingSessionMetadata(targetSessionId, metadata)
    codingSessions.value = codingSessions.value.map((item) =>
      item.session_id === targetSessionId ? summary : item,
    )
    return summary
  }

  function renameSession(targetSessionId: string, title: string) {
    return updateSessionMetadata(targetSessionId, { title })
  }

  function setSessionPinned(targetSessionId: string, pinned: boolean) {
    return updateSessionMetadata(targetSessionId, { pinned })
  }

  function setSessionArchived(targetSessionId: string, archived: boolean) {
    return updateSessionMetadata(targetSessionId, { archived })
  }

  async function selectSession(targetSessionId: string) {
    if (!targetSessionId || targetSessionId === sessionId.value) return
    const selection = ++selectionGeneration
    // A resident UI state is not proof that its server-side session still exists.
    const session = await resumeCodingSession(targetSessionId)
    if (selection !== selectionGeneration) return
    stopSessionTransport()
    sessionId.value = session.session_id
    ensureSession(session.session_id)
    workspaceRoot.value = session.workspace_root
    workspaceId.value = session.workspace_id
    permissionMode.value = session.permission_mode
    runtimeProfile.value = session.runtime_profile || 'legacy'
    runs.value = []
    selectedRun.value = null
    diffDrawerVisible.value = false
    currentDiffData.value = null
    dirCache.clear()
    await Promise.all([
      loadTimeline(targetSessionId),
      loadModels(targetSessionId),
      loadGitStatus(),
      loadFiles('.', true),
      loadSessions().catch(() => {}),
      loadRuns(),
      loadContext(),
    ])
    if (selection !== selectionGeneration || sessionId.value !== targetSessionId) return
    connectSocket()
  }

  async function createNewSession(
    initialPrompt = '',
    initialSurfaceContext?: HarnessSurfaceContext | null,
  ): Promise<string> {
    const selection = ++selectionGeneration
    await bootstrapModelCatalog()
    if (selection !== selectionGeneration) return ''
    const session = await startCodingSession(
      undefined,
      'ask',
      hasNewSessionRuntimePreference ? newSessionRuntimeProfile.value : undefined,
    )
    if (selection !== selectionGeneration) return ''
    stopSessionTransport()
    sessionId.value = session.session_id
    ensureSession(session.session_id)
    workspaceRoot.value = session.workspace_root
    workspaceId.value = session.workspace_id
    permissionMode.value = session.permission_mode
    runtimeProfile.value = session.runtime_profile || 'legacy'
    runs.value = []
    selectedRun.value = null
    diffDrawerVisible.value = false
    currentDiffData.value = null
    dirCache.clear()
    await Promise.all([
      loadTimeline(session.session_id),
      loadModels(session.session_id),
      loadGitStatus(),
      loadFiles('.', true),
      loadSessions().catch(() => {}),
      loadRuns(),
      loadContext(),
    ])
    if (selection !== selectionGeneration || sessionId.value !== session.session_id) return ''
    if (initialPrompt) {
      pendingInitialPrompts.set(session.session_id, {
        content: initialPrompt,
        surfaceContext: initialSurfaceContext,
      })
    }
    connectSocket()
    return session.session_id
  }

  function startNewSession(): Promise<string> {
    return createNewSession()
  }

  async function startSessionWithPrompt(
    content: string,
    surfaceContext?: HarnessSurfaceContext | null,
  ): Promise<string> {
    const prompt = content.trim()
    if (!prompt) throw new Error('请输入内容后再开始对话')
    return createNewSession(prompt, surfaceContext)
  }

  async function restoreCurrentSession() {
    const targetSessionId = sessionId.value
    if (!targetSessionId) return
    const selection = ++selectionGeneration
    await Promise.all([
      loadTimeline(targetSessionId),
      loadModels(targetSessionId),
      loadGitStatus(),
      loadFiles('.', true),
      loadSessions().catch(() => {}),
      loadRuns(),
      loadContext(targetSessionId),
    ])
    if (selection !== selectionGeneration || sessionId.value !== targetSessionId) return
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
    const targetSessionId = sessionId.value
    if (!targetSessionId) return false
    const generation = ++runDetailGeneration
    try {
      const detail = await fetchCodingRun(targetSessionId, runId)
      if (targetSessionId !== sessionId.value || generation !== runDetailGeneration) return false
      selectedRun.value = detail
      return true
    } catch {
      if (targetSessionId !== sessionId.value || generation !== runDetailGeneration) return false
      selectedRun.value = null
      return false
    }
  }

  async function loadRunDiff(runId: string) {
    const targetSessionId = sessionId.value
    if (!targetSessionId) return
    const generation = ++runDiffGeneration
    try {
      const diff = await fetchCodingRunDiff(targetSessionId, runId)
      if (targetSessionId !== sessionId.value || generation !== runDiffGeneration) return
      currentDiffData.value = diff
      diffDrawerVisible.value = true
    } catch (e) {
      if (targetSessionId !== sessionId.value || generation !== runDiffGeneration) return
      errorMessage.value = String(e)
    }
  }

  function openDiffDrawer() {
    if (lastDiffInfo.value) {
      openRunDiff(lastDiffInfo.value.run_id)
    }
  }

  function openRunDiff(runId: string) {
    if (runId) void loadRunDiff(runId)
  }

  function closeDiffDrawer() {
    diffDrawerVisible.value = false
  }

  async function loadGitStatus() {
    const targetSessionId = sessionId.value
    if (!targetSessionId) return
    const generation = ++gitRequestGeneration
    try {
      const status = await fetchCodingGitStatus(targetSessionId)
      if (targetSessionId !== sessionId.value || generation !== gitRequestGeneration) return
      gitStatus.value = status
    } catch {
      if (targetSessionId !== sessionId.value || generation !== gitRequestGeneration) return
      gitStatus.value = { is_git: false, branch: '', dirty_count: 0, changed_files: [] }
    }
  }

  async function loadFiles(path: string, force = false) {
    const targetSessionId = sessionId.value
    if (!targetSessionId) return
    const generation = ++fileTreeGeneration
    const cacheKey = `${targetSessionId}:${path}`
    if (!force && dirCache.has(cacheKey)) {
      fileTreePath.value = path
      fileTreeEntries.value = filterFileEntries([...(dirCache.get(cacheKey) || [])])
      expandedDirs.value = new Set([...expandedDirs.value, path])
      return
    }
    try {
      const res = await fetchCodingFiles(targetSessionId, path)
      if (targetSessionId !== sessionId.value || generation !== fileTreeGeneration) return
      const entries = filterFileEntries(res.entries)
      dirCache.set(cacheKey, entries)
      fileTreePath.value = path
      fileTreeEntries.value = entries
      expandedDirs.value = new Set([...expandedDirs.value, path])
    } catch {
      if (targetSessionId !== sessionId.value || generation !== fileTreeGeneration) return
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
    const targetSessionId = sessionId.value
    if (!targetSessionId) return
    const generation = ++filePreviewGeneration
    try {
      const res = await fetchCodingFile(targetSessionId, path)
      if (targetSessionId !== sessionId.value || generation !== filePreviewGeneration) return
      previewPath.value = path
      previewContent.value = res.content
    } catch {
      if (targetSessionId !== sessionId.value || generation !== filePreviewGeneration) return
      previewPath.value = path
      previewContent.value = '无法加载文件'
    }
  }

  async function changeModel(modelId: string) {
    const targetSessionId = sessionId.value
    if (!targetSessionId) return
    const state = ensureSession(targetSessionId)
    const generation = ++state.modelMutationGeneration
    try {
      const result = await switchCodingModel(targetSessionId, modelId)
      if (generation !== state.modelMutationGeneration) return
      state.currentModelId = modelId
      state.reasoningMode = result.reasoning_mode
      await loadContext(targetSessionId)
    } catch (error) {
      if (generation === state.modelMutationGeneration) state.errorMessage = String(error)
    }
  }

  async function changeReasoning(mode: 'off' | 'low' | 'medium' | 'high') {
    const targetSessionId = sessionId.value
    if (!targetSessionId || isThinking.value) return false
    const state = ensureSession(targetSessionId)
    const generation = ++state.modelMutationGeneration
    try {
      const result = await switchCodingReasoning(targetSessionId, mode)
      if (generation !== state.modelMutationGeneration) return false
      state.reasoningMode = result.reasoning_mode
      return true
    } catch (error) {
      if (generation === state.modelMutationGeneration) state.errorMessage = String(error)
      return false
    }
  }

  async function compactContext(): Promise<boolean> {
    if (!sessionId.value || isThinking.value || contextBusy.value || !contextCompactable.value) {
      return false
    }
    const targetSessionId = sessionId.value
    const state = ensureSession(targetSessionId)
    const requestGeneration = ++state.compactionGeneration
    state.compactionState = 'running'
    state.compactionError = ''
    try {
      const response = await requestCodingCompaction(targetSessionId)
      if (requestGeneration !== state.compactionGeneration) return false
      state.contextSnapshot = response.context
      state.contextChars = response.after_tokens
      state.compactionState = response.applied ? 'succeeded' : 'failed'
      if (!response.applied) state.compactionError = contextReasonLabel(response.reason)
      return response.applied
    } catch (error) {
      if (requestGeneration !== state.compactionGeneration) return false
      state.compactionState = 'failed'
      state.compactionError = String(error)
      state.errorMessage = state.compactionError
      return false
    }
  }

  async function changePermissionMode(mode: PermissionMode): Promise<boolean> {
    const targetSessionId = sessionId.value
    if (!targetSessionId || isThinking.value) return false
    const state = ensureSession(targetSessionId)
    const generation = ++state.permissionMutationGeneration
    try {
      const result = await switchPermissionMode(targetSessionId, mode)
      if (generation !== state.permissionMutationGeneration) return false
      state.permissionMode = result.mode
      return true
    } catch (error) {
      if (generation === state.permissionMutationGeneration) state.errorMessage = String(error)
      return false
    }
  }

  function disconnect() {
    selectionGeneration += 1
    stream?.disconnect()
    stream = null
    stopApprovalPolling()
    for (const timer of scheduledTimeouts) window.clearTimeout(timer)
    scheduledTimeouts.clear()
    liveRefreshPending.clear()
    memoryRefreshPending.clear()
    terminalRefreshPending.clear()
    pendingInitialPrompts.clear()
  }

  return {
    sessionId,
    sessionsById,
    workspaceRoot,
    workspaceId,
    messages,
    optimisticMessage,
    legacyMessages,
    timeline,
    olderTimeline,
    visibleTimeline,
    turns,
    timelineCursor,
    olderCursor,
    timelineHasMore,
    timelineInitialized,
    timelineLoading,
    activeRun,
    connectionState,
    scrollAnchor,
    isThinking,
    errorMessage,
    currentModelId,
    reasoningMode,
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
    runtimeProfile,
    planTopic,
    planPath,
    planReview,
    lastDiffInfo,
    diffInfoByRun,
    diffDrawerVisible,
    currentDiffData,
    codingSessions,
    runs,
    selectedRun,
    memoryProposals,
    memoryProposalBusy,
    memoryProposalError,
    knowledgeSourceProposals,
    knowledgeSourceProposalDetails,
    knowledgeSourceProposalBusy,
    knowledgeSourceProposalDetailBusy,
    knowledgeSourceProposalError,
    skills,
    mcpServers,
    models,
    availableRuntimeProfiles,
    newSessionRuntimeProfile,
    providerSettings,
    accountProviders,
    accountDefaultModel,
    accountProviderAuthenticated,
    accountProviderLoading,
    accountProviderError,
    usageSummary,
    usageRange,
    providerSettingsError,
    usageError,
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
    handleTimelineEvent,
    mergeTimelinePage,
    loadTimeline,
    loadOlderTimeline,
    loadMoreTimeline,
    setScrollAnchor,
    respondApproval,
    stopCurrentRun,
    approvePlan,
    rejectPlan,
    selectSession,
    startNewSession,
    startSessionWithPrompt,
    restoreCurrentSession,
    connectSocket,
    loadSkills,
    loadMcpServers,
    loadModels,
    bootstrapModelCatalog,
    setNewSessionRuntimeProfile,
    loadProviderSettings,
    loadModelProviderSettings,
    loadUsage,
    saveProviderSettings,
    loadContext,
    loadSessions,
    updateSessionMetadata,
    renameSession,
    setSessionPinned,
    setSessionArchived,
    loadRuns,
    loadMemoryProposals,
    approveMemoryProposal: approveMemoryProposalAction,
    rejectMemoryProposal: rejectMemoryProposalAction,
    loadKnowledgeSourceProposals,
    loadKnowledgeSourceProposalDetail,
    approveKnowledgeSourceProposal: approveKnowledgeSourceProposalAction,
    rejectKnowledgeSourceProposal: rejectKnowledgeSourceProposalAction,
    loadRunDetail,
    loadRunDiff,
    openDiffDrawer,
    openRunDiff,
    closeDiffDrawer,
    loadGitStatus,
    loadFiles,
    refreshWorkspaceView,
    loadFilePreview,
    changeModel,
    changeReasoning,
    compactContext,
    changePermissionMode,
    disconnect,
  }
})
