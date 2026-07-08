export type ProgressEvent = {
  type: 'progress'
  agent: string
  message: string
}

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

export type CodingToolCallEvent = {
  type: 'tool_call'
  tool: string
  args: Record<string, unknown>
}

export type CodingToolResultEvent = {
  type: 'tool_result'
  tool: string
  args: Record<string, unknown>
  content: string
  is_error: boolean
}

export type CodingFinalEvent = {
  type: 'final'
  content: string
}

export type CodingStepLimitEvent = {
  type: 'step_limit'
  content: string
}

export type CodingCancelledEvent = {
  type: 'cancelled'
  content: string
}

export type CodingTraceEvent = {
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

export type CodingApprovalRequiredEvent = {
  type: 'approval_required'
  approval_id: string
  tool: string
  args: Record<string, unknown>
  description: string
  pattern_key: string
}

export type CodingServerEvent =
  | CodingToolCallEvent
  | CodingToolResultEvent
  | CodingFinalEvent
  | CodingStepLimitEvent
  | CodingCancelledEvent
  | ErrorEvent
  | CodingTraceEvent
  | CodingSkillInvokedEvent
  | CodingApprovalRequiredEvent

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
}

export type CodingModelsResponse = {
  models: CodingModel[]
  current: string | null
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
