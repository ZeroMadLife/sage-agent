export type ProgressEvent = {
  type: 'progress'
  agent: string
  message: string
}

export type PermissionMode = 'default' | 'accept_edits' | 'auto' | 'plan'

export type ToolCallEvent = {
  type: 'tool_call'
  tool: string
  args: Record<string, unknown>
  status: 'running' | 'done' | 'error'
  message: string
}

export type ToolCallStatus = {
  tool: string
  args: Record<string, unknown>
  status: 'running' | 'done' | 'error'
  message?: string
}

export type VerificationIssue = {
  code: string
  message: string
  severity: string
}

export type VerificationResult = {
  passed: boolean
  issues: VerificationIssue[]
}

export type BudgetBreakdown = {
  total: number
  spent: number
  transport?: number
  accommodation?: number
  food?: number
  tickets?: number
  misc?: number
  over_budget: boolean
}

export type SpotVisit = {
  spot_id: string
  name: string
  arrival_time: string
  departure_time: string
  duration_hours?: number
  ticket_price: number
  category?: string
  location?: string
}

export type Meal = {
  name: string
  meal_type: string
  estimated_cost: number
}

export type Transport = {
  from_name: string
  to_name: string
  mode: string
  distance_m: number
  duration_s: number
}

export type ItineraryDay = {
  date: string
  spots: SpotVisit[]
  meals?: Meal[]
  transport?: Transport[]
  total_cost: number
}

export type Itinerary = {
  destination: string
  days: ItineraryDay[]
  total_cost: number
  weather_summary?: string
  budget?: BudgetBreakdown | null
}

export type AgentResultEvent = {
  type: 'result'
  content: string
  itinerary: Itinerary | null
  tool_calls: Array<{ tool: string; error: string }>
  metrics: Record<string, unknown>
}

export type ErrorEvent = {
  type: 'error'
  message: string
  recoverable: boolean
}

export type BusyEvent = {
  type: 'busy'
  message: string
}

export type ServerEvent = ProgressEvent | ToolCallEvent | AgentResultEvent | ErrorEvent | BusyEvent

export type ChatStartResponse = {
  session_id: string
}

export type CodingSessionResponse = {
  session_id: string
  workspace_root: string
  permission_mode: PermissionMode
}

export type CodingSessionSummary = {
  session_id: string
  title: string
  workspace_root: string
  created_at: string
  updated_at: string
  runtime_mode: string
  message_count: number
}

export type CodingSessionsResponse = {
  sessions: CodingSessionSummary[]
}

export type CodingSessionMessage = {
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export type CodingSessionMessagesResponse = {
  messages: CodingSessionMessage[]
}

export type CodingApproval = {
  approval_id: string
  session_id: string
  tool: string
  args: Record<string, unknown>
  description: string
  pattern_key: string
  diff_preview?: CodingDiffLine[]
}

export type CodingApprovalResponse = CodingApproval | null

export type CodingApprovalChoice = 'once' | 'session' | 'always' | 'deny'

export type CodingDiffLine = {
  type: 'context' | 'add' | 'remove'
  text: string
}

export type CodingEventMeta = {
  run_id?: string
  created_at?: string
}

export type CodingToolCallEvent = CodingEventMeta & {
  type: 'tool_call'
  tool: string
  args: Record<string, unknown>
}

export type CodingToolResultEvent = CodingEventMeta & {
  type: 'tool_result'
  tool: string
  args: Record<string, unknown>
  content: string
  is_error: boolean
  policy_reason?: string | null
  security_event_type?: string | null
}

export type CodingFinalEvent = CodingEventMeta & {
  type: 'final'
  content: string
}

export type CodingStepLimitEvent = CodingEventMeta & {
  type: 'step_limit'
  content: string
}

export type CodingCancelledEvent = CodingEventMeta & {
  type: 'cancelled'
  content: string
}

export type CodingTraceEvent = CodingEventMeta & {
  type: 'model_requested' | 'model_parsed' | 'retry'
  content?: string
  kind?: string
  prompt_chars?: number
}

export type CodingSkillInvokedEvent = {
  type: 'skill_invoked'
  skill: string
  arguments: string
}

export type CodingApprovalRequiredEvent = CodingEventMeta & {
  type: 'approval_required'
  approval_id: string
  tool: string
  args: Record<string, unknown>
  description: string
  pattern_key: string
}

export type CodingApprovalGrantedEvent = CodingEventMeta & {
  type: 'approval_granted'
  tool: string
}

export type CodingTurnEvent = CodingEventMeta & {
  type: 'turn_started' | 'turn_finished'
}

export type CodingTextDeltaEvent = CodingEventMeta & {
  type: 'text_delta'
  delta: string
}

export type CodingRuntimeModeChangedEvent = CodingEventMeta & {
  type: 'runtime_mode_changed'
  mode: string
  topic?: string
  plan_path?: string
}

export type CodingPlanReadyForReviewEvent = CodingEventMeta & {
  type: 'plan_ready_for_review'
  review_id: string
  plan_path: string
  summary: string
}

export type CodingWorkspaceDiffReadyEvent = CodingEventMeta & {
  type: 'workspace_diff_ready'
  changed_files: string[]
  file_count: number
  truncated: boolean
}

export type CodingRunFinishedEvent = CodingEventMeta & {
  type: 'run_finished'
  status: string
  duration_ms: number
  tool_steps: number
}

export type CodingContextUsageEvent = CodingEventMeta & {
  type: 'context_usage_updated'
  session_id: string
  used_tokens: number
  model_limit_tokens: number
  output_reserve_tokens: number
  effective_limit_tokens: number
  usage_ratio: number
  level: string
  estimated: boolean
  compactable: boolean
}

export type CodingCompactionStartedEvent = CodingEventMeta & {
  type: 'context_compaction_started'
  session_id: string
  compaction_id: string
  trigger: string
  before_tokens: number
}

export type CodingCompactionCompletedEvent = CodingEventMeta & {
  type: 'context_compaction_completed'
  session_id: string
  compaction_id: string
  before_tokens: number
  after_tokens: number
  archived_items: number
  saved_ratio?: number
}

export type CodingCompactionFailedEvent = CodingEventMeta & {
  type: 'context_compaction_failed'
  session_id: string
  compaction_id: string
  reason: string
  preserved_original: boolean
  retryable: boolean
}

export type CodingErrorEvent = CodingEventMeta & {
  type: 'error'
  message: string
  recoverable?: boolean
}

export type CodingServerEvent =
  | CodingToolCallEvent
  | CodingToolResultEvent
  | CodingFinalEvent
  | CodingStepLimitEvent
  | CodingCancelledEvent
  | CodingErrorEvent
  | CodingTraceEvent
  | CodingSkillInvokedEvent
  | CodingApprovalRequiredEvent
  | CodingApprovalGrantedEvent
  | CodingTurnEvent
  | CodingTextDeltaEvent
  | CodingRuntimeModeChangedEvent
  | CodingPlanReadyForReviewEvent
  | CodingWorkspaceDiffReadyEvent
  | CodingRunFinishedEvent
  | CodingContextUsageEvent
  | CodingCompactionStartedEvent
  | CodingCompactionCompletedEvent
  | CodingCompactionFailedEvent

export type CodingFileEntry = {
  name: string
  is_dir: boolean
}

export type CodingFilesResponse = {
  path: string
  entries: CodingFileEntry[]
}

export type CodingFileContentResponse = {
  path: string
  content: string
  lines: number
}

export type CodingGitStatusResponse = {
  is_git: boolean
  branch: string
  dirty_count: number
  changed_files: string[]
}

export type CodingModel = {
  id: string
  label: string
  provider: string
  context_window_tokens?: number | null
  output_reserve_tokens?: number | null
  context_configured?: boolean
}

export type CodingModelsResponse = {
  models: CodingModel[]
  current: string | null
}

export type CodingContextSnapshot = {
  model_id?: string | null
  configured: boolean
  used_tokens: number | null
  model_limit_tokens: number | null
  effective_limit_tokens: number | null
  output_reserve_tokens: number | null
  usage_ratio: number | null
  level: string
  estimated: boolean | null
  compactable: boolean
  active_run_id: string | null
  context_operation_active: boolean
  checkpoint_id: string | null
  resume_status: string
  checkpoint_resume_enabled: boolean
  latest_attempt?: Record<string, unknown> | null
  stale_started: boolean
}

export type CodingCompactResponse = {
  compaction_id: string
  applied: boolean
  before_tokens: number
  after_tokens: number
  archived_items: number
  reason: string
  retryable: boolean
  context: CodingContextSnapshot
}

export type CodingSkillSummary = {
  name: string
  description: string
  source: string
  argument_hint: string
}

export type CodingSkillsResponse = {
  skills: CodingSkillSummary[]
}

export type CodingSkillDetailResponse = {
  name: string
  description: string
  source: string
  content: string
}

export type CodingMcpServer = {
  name: string
  transport: string
  status: string
}

export type CodingMcpServersResponse = {
  servers: CodingMcpServer[]
}

export type CodingRunSummary = {
  run_id: string
  status: string
  event_count: number
  tool_count: number
  error_count: number
  last_event_type: string
  started_at: string
  updated_at: string
  changed_files?: string[]
}

export type CodingFileDiff = {
  path: string
  status: 'added' | 'modified' | 'deleted'
  before_hash: string
  after_hash: string
  diff: string
  truncated: boolean
  binary: boolean
  ignored_sensitive: boolean
}

export type CodingRunDiff = {
  run_id: string
  changed_files: CodingFileDiff[]
  file_count: number
  truncated: boolean
}

export type CodingRunsResponse = {
  runs: CodingRunSummary[]
}

export type CodingRunTimelineEntry = {
  kind: string
  title: string
  detail: string
  status: string
  tool: string
  timestamp: string
}

export type CodingRunDetailResponse = {
  run_id: string
  events: Array<Record<string, unknown>>
  timeline: CodingRunTimelineEntry[]
}

export type SessionSummary = {
  session_id: string
  title: string
  created_at: string
  updated_at: string
  status: string
}

export type SessionListResponse = {
  sessions: SessionSummary[]
}

export type HistoryMessage = {
  role: string
  content: string
  tool_calls: Array<Record<string, unknown>> | null
  created_at: string
}

export type SessionMessagesResponse = {
  messages: HistoryMessage[]
}

export type HistoryItinerary = {
  id: number
  destination: string
  total_cost: number
  created_at: string
  content: Itinerary
}

export type ItineraryListResponse = {
  itineraries: HistoryItinerary[]
}
