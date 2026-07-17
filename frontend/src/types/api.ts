export type ProgressEvent = {
  type: 'progress'
  agent: string
  message: string
}

export type PermissionMode = 'default' | 'accept_edits' | 'auto' | 'plan'
export type CodingRuntimeProfile = 'legacy' | 'deerflow_v2'

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
  workspace_id: string
  permission_mode: PermissionMode
  runtime_profile: CodingRuntimeProfile
  sandbox_provider?: string
  sandbox_image?: string
}

export type CodingSessionSummary = {
  session_id: string
  title: string
  workspace_root: string
  created_at: string
  updated_at: string
  runtime_mode: string
  runtime_profile: CodingRuntimeProfile
  message_count: number
  pinned?: boolean
  archived?: boolean
}

export type CodingSessionsResponse = {
  sessions: CodingSessionSummary[]
}

export type AssistantHomeSectionStatus =
  | 'ready'
  | 'empty'
  | 'not_configured'
  | 'unavailable'
  | 'error'

export type AssistantHomeSummary = {
  identity: {
    mode: 'local' | 'cloud'
    user_id: string | null
    display_name: string
  }
  knowledge: {
    status: AssistantHomeSectionStatus
    source_count: number
    wiki_page_count: number
    last_synced_at: string | null
  }
  sessions: {
    status: AssistantHomeSectionStatus
    items: Array<{
      session_id: string
      title: string
      workspace_name: string
      updated_at: string
      message_count: number
      target: string
    }>
    total: number
    error: string | null
  }
  projects: {
    status: AssistantHomeSectionStatus
    items: Array<{ project_id: string; name: string }>
    total: number
    error: string | null
  }
  proposals: {
    status: AssistantHomeSectionStatus
    memory_pending: number
    wiki_pending: number
    note_pending: number
    error: string | null
  }
  suggested_actions: Array<{
    id: string
    kind: 'chat' | 'knowledge' | 'review' | 'project'
    label: string
    description: string
    target: string
  }>
}

export type KnowledgeSourceRoot = {
  root_id: string
  kind: 'obsidian' | 'markdown' | 'github' | 'feishu'
  label: string
}

export type KnowledgeWorkspaceSummary = {
  status: 'ready'
  workspace_name: string
  source_count: number
  wiki_page_count: number
  pending_proposal_count: number
  last_synced_at: string | null
  source_roots: KnowledgeSourceRoot[]
}

export type KnowledgeIndexSummary = {
  status: 'ready' | 'degraded'
  backend: string
  embedding_model: string
  embedding_revision: string
  revision_count: number
  indexed_revision_count: number
  active_chunk_count: number
  total_chunk_count: number
  error_count: number
}

export type KnowledgeGraphNodeKind =
  | 'page'
  | 'source'
  | 'project'
  | 'concept'
  | 'decision'
  | 'tool'

export type KnowledgeGraphSnapshot = {
  graph_revision: string
  workspace_id: string
  wiki_watermark: string
  projector_id: string
  projector_version: string
  config_hash: string
  status: 'building' | 'ready' | 'error'
  node_count: number
  edge_count: number
  warning_count: number
  error: string | null
  created_at: string
  completed_at: string | null
  stale: boolean
}

export type KnowledgeGraphEvidence = {
  citation_id: string
  chunk_id: string
  page_id: string
  page_revision: string
  source_id: string
  source_revision: string
}

export type KnowledgeGraphNode = {
  node_id: string
  kind: KnowledgeGraphNodeKind
  label: string
  page_id: string | null
  page_revision: string | null
  source_id: string | null
  source_revision: string | null
  properties: Record<string, unknown>
}

export type KnowledgeGraphEdge = {
  edge_id: string
  source_node_id: string
  target_node_id: string
  kind: 'WIKILINK' | 'EVIDENCED_BY' | 'SHARES_SOURCE'
  directed: boolean
  weight: number
  confidence: number
  extractor_id: string
  extractor_version: string
  properties: Record<string, unknown>
  evidence: KnowledgeGraphEvidence[]
}

export type KnowledgeGraph = {
  snapshot: KnowledgeGraphSnapshot
  nodes: KnowledgeGraphNode[]
  edges: KnowledgeGraphEdge[]
  offset: number
  next_offset: number | null
  has_more: boolean
}

export type KnowledgeLearningCapability = {
  capability_id: string
  label: string
  description: string
  keywords: string[]
  weight: number
  required: boolean
}

export type KnowledgeLearningGoal = {
  schema_version: number
  goal_id: string
  title: string
  description: string
  capabilities: KnowledgeLearningCapability[]
  goal_revision: string
  git_commit: string
  structured: boolean
}

export type KnowledgeGraphAnalysisSnapshot = {
  analysis_revision: string
  workspace_id: string
  graph_revision: string
  goal_revision: string
  algorithm_id: string
  algorithm_version: string
  seed: number
  resolution: number
  threshold: number
  status: 'building' | 'ready' | 'error'
  community_count: number
  insight_count: number
  error: string | null
  created_at: string
  completed_at: string | null
}

export type KnowledgeGraphCommunity = {
  community_id: string
  label: string
  node_count: number
  edge_count: number
  cohesion: number
  properties: Record<string, unknown>
}

export type KnowledgeGraphNodeMetric = {
  node_id: string
  community_id: string
  degree: number
  weighted_degree: number
  bridge_score: number
}

export type KnowledgeGraphCommunities = {
  analysis: KnowledgeGraphAnalysisSnapshot
  communities: KnowledgeGraphCommunity[]
  node_metrics: KnowledgeGraphNodeMetric[]
}

export type KnowledgeGoalAlignment = {
  capability_id: string
  label: string
  coverage: number
  status: 'covered' | 'learning' | 'gap'
  matched_keywords: string[]
  missing_keywords: string[]
  matched_node_ids: string[]
}

export type KnowledgeGraphInsightKind =
  | 'missing_concept'
  | 'isolated_node'
  | 'bridge_node'
  | 'sparse_community'
  | 'capability_gap'

export type KnowledgeGraphInsight = {
  insight_id: string
  kind: KnowledgeGraphInsightKind
  severity: 'low' | 'medium' | 'high'
  title: string
  description: string
  node_id: string | null
  community_id: string | null
  capability_id: string | null
  properties: Record<string, unknown>
}

export type KnowledgeGraphInsights = {
  analysis: KnowledgeGraphAnalysisSnapshot
  goal: KnowledgeLearningGoal
  alignments: KnowledgeGoalAlignment[]
  insights: KnowledgeGraphInsight[]
}

export type KnowledgeGraphNeighborhood = {
  snapshot: KnowledgeGraphSnapshot
  center: KnowledgeGraphNode
  nodes: KnowledgeGraphNode[]
  edges: KnowledgeGraphEdge[]
}

export type KnowledgeEvidence = {
  citation_id: string
  rank: number
  rrf_score: number
  sparse_rank: number | null
  sparse_score: number | null
  dense_rank: number | null
  dense_score: number | null
  chunk_id: string
  page_id: string
  page_revision: string
  page_path: string
  source_id: string
  source_revision: string
  source_kind: string
  source_relative_path: string
  proposal_id: string
  artifact_id: string | null
  block_id: string
  ordinal: number
  title: string
  heading_path: string[]
  page_number: number | null
  excerpt: string
  token_count: number
  truncated: boolean
}

export type KnowledgeRetrieval = {
  query: string
  status: 'evidence_found' | 'no_evidence'
  token_budget: number
  used_tokens: number
  omitted_count: number
  citations: KnowledgeEvidence[]
}

export type KnowledgeCitation = {
  citation_id: string
  chunk_id: string
  page_id: string
  page_revision: string
  page_path: string
  source_id: string
  source_revision: string
  source_kind: string
  source_relative_path: string
  block_id: string
  ordinal: number
  title: string
  heading_path: string[]
  page_number: number | null
  excerpt: string
  token_count: number
  truncated: boolean
}

export type KnowledgeMigrationPlanItem = {
  proposal_id: string
  source_root_id: string
  source_relative_path: string
  disposition: 'auto_apply' | 'retire' | 'review' | 'block'
  reason_codes: string[]
  parser_id: string | null
}

export type KnowledgeMigrationPlan = {
  plan_id: string
  total: number
  auto_apply_count: number
  retire_count: number
  review_count: number
  block_count: number
  items: KnowledgeMigrationPlanItem[]
}

export type KnowledgeMigrationResult = {
  plan_id: string
  status: 'completed' | 'completed_with_errors'
  total: number
  auto_applied_count: number
  retired_count: number
  review_count: number
  blocked_count: number
  error_count: number
  items: Array<{
    proposal_id: string
    status: 'auto_applied' | 'retired' | 'review' | 'blocked' | 'error'
    replacement_proposal_id: string | null
    reason_code: string | null
  }>
}

export type KnowledgeProposal = {
  proposal_id: string
  source_root_id: string
  source_kind: string
  source_relative_path: string
  source_revision: string
  raw_path: string
  page_id: string
  target_path: string
  title: string
  base_page_revision: string
  change_kind: 'ingest' | 'rollback' | 'synthesis' | 'retraction' | 'learning'
  status: 'pending' | 'approved' | 'rejected'
  projection_status: 'pending' | 'complete' | 'error'
  revision: number
  parse_artifact_id: string | null
  error: string | null
  policy_decision: {
    decision_id: string
    policy_id: string
    policy_version: string
    risk_level: 'low' | 'medium' | 'high' | 'blocked'
    action: 'auto_apply' | 'draft' | 'require_review' | 'block'
    reason_codes: string[]
    applied_page_revision: string | null
    undo_available: boolean
    undo_proposal_id: string | null
    undo_page_revision: string | null
    undone_at: string | null
  } | null
  diff: string
  diff_truncated: boolean
  created_at: string
  updated_at: string
}

export type KnowledgeSyncChange = {
  relative_path: string
  change_kind: 'added' | 'modified' | 'deleted'
  previous_revision: string | null
  source_revision: string | null
  idempotency_key: string
}

export type KnowledgeSyncPlan = {
  plan_id: string
  workspace_id: string
  source_root_id: string
  relative_directory: string
  pipeline_version: string
  base_watermark: number
  target_watermark: number
  manifest_hash: string
  status: string
  added_count: number
  modified_count: number
  deleted_count: number
  total_count: number
  has_more: boolean
  changes: KnowledgeSyncChange[]
  created_at: string
}

export type KnowledgeJobStatus =
  | 'queued'
  | 'running'
  | 'cancelling'
  | 'completed'
  | 'completed_with_errors'
  | 'cancelled'

export type KnowledgeJobItem = {
  item_id: string
  job_id: string
  relative_path: string
  source_revision: string
  change_kind: 'added' | 'modified' | 'deleted'
  status: string
  attempts: number
  max_attempts: number
  proposal_id: string | null
  error: string | null
  next_attempt_at: string | null
  updated_at: string
}

export type KnowledgeJob = {
  job_id: string
  workspace_id: string
  source_root_id: string
  source_kind: string
  source_label: string
  relative_directory: string
  pipeline_version: string
  status: KnowledgeJobStatus
  cancel_requested: boolean
  total_items: number
  processed_items: number
  succeeded_items: number
  skipped_items: number
  failed_items: number
  cancelled_items: number
  latest_sequence: number
  created_at: string
  started_at: string | null
  completed_at: string | null
  updated_at: string
  sync_plan_id?: string | null
  items: KnowledgeJobItem[]
}

export type KnowledgeJobEvent = {
  event_id: string
  job_id: string
  item_id: string | null
  sequence: number
  kind: string
  status: string
  detail: Record<string, string | number | boolean | null>
  created_at: string
}

export type KnowledgePageRevision = {
  revision_id: string
  sequence: number
  content_hash: string
  source_revision: string
  proposal_id: string
  change_kind: 'ingest' | 'rollback' | 'synthesis' | 'retraction' | 'learning'
  git_commit: string
  created_at: string
}

export type KnowledgePage = {
  page_id: string
  path: string
  title: string
  current_revision: string
  updated_at: string
  revisions: KnowledgePageRevision[]
}

export type KnowledgePageDocument = {
  page_id: string
  path: string
  title: string
  updated_at: string
  revision: KnowledgePageRevision
  content: string
  truncated: boolean
}

export type CodingSessionMessage = {
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export type CodingSessionMessagesResponse = {
  messages: CodingSessionMessage[]
}

export type CodingTimelineKind =
  | 'user'
  | 'assistant'
  | 'model'
  | 'tool'
  | 'approval'
  | 'context'
  | 'memory'
  | 'agent'
  | 'terminal'
  | 'system'
  | 'run'
  | 'harness'

export type CodingTimelineStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'blocked'
  | 'done'
  | 'completed'
  | 'cancelled'
  | 'error'
  | 'interrupted'
  | 'retryable'

export type CodingTimelineEvent = {
  event_id: string
  session_id: string
  run_id: string
  sequence: number
  kind: CodingTimelineKind
  status: CodingTimelineStatus
  timestamp: string
  payload: Record<string, unknown> & { type?: string }
}

export type CodingActiveRun = {
  run_id: string
  status: 'running'
}

export type CodingTimelineResponse = {
  items: CodingTimelineEvent[]
  next_cursor: number
  has_more: boolean
  older_cursor: number | null
  latest_cursor: number
  active_run: CodingActiveRun | null
}

export type CodingApproval = {
  approval_id: string
  session_id: string
  tool: string
  args: Record<string, unknown>
  description: string
  pattern_key: string
  run_id?: string
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
  tool_call_id?: string
  tool: string
  args: Record<string, unknown>
}

export type CodingToolResultEvent = CodingEventMeta & {
  type: 'tool_result'
  tool_call_id?: string
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

export type CodingMemoryProposalReadyEvent = CodingEventMeta & {
  type: 'memory_proposal_ready'
  session_id: string
  run_id: string
  reflection_id: string
  proposal_id: string
  candidate_count: number
  base_revision: number
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
  level: 'normal' | 'budget' | 'snip' | 'compact' | 'high' | 'emergency'
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
  saved_ratio: number
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
  | CodingMemoryProposalReadyEvent
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
  context_window_tokens: number | null
  output_reserve_tokens: number | null
  context_configured: boolean
  reasoning_modes: Array<'low' | 'medium' | 'high'>
}

export type CodingModelsResponse = {
  models: CodingModel[]
  current: string | null
  reasoning_mode: 'off' | 'low' | 'medium' | 'high'
  runtime_profiles: CodingRuntimeProfile[]
  default_runtime_profile: CodingRuntimeProfile
}

export type CodingProviderReasoning =
  | { kind: 'unsupported' }
  | { kind: 'openai_reasoning_effort'; modes: Array<'low' | 'medium' | 'high'> }
  | { kind: 'anthropic_thinking_budget'; budgets: Partial<Record<'low' | 'medium' | 'high', number>> }

export type CodingProviderModel = {
  id: string
  label: string
  context_window_tokens: number | null
  output_reserve_tokens: number | null
  reasoning: CodingProviderReasoning
}

export type CodingProvider = {
  id: string
  label: string
  api_mode: 'openai_chat_completions' | 'anthropic_messages'
  base_url: string
  api_key_env: string
  api_key_configured: boolean
  models: CodingProviderModel[]
}

export type CodingProviderSettings = {
  version: 1
  default_model: string
  source: 'legacy_toml' | 'project_json' | 'deployment_json'
  editable: boolean
  providers: CodingProvider[]
}

export type CodingProviderSettingsUpdate = {
  version: 1
  default_model: string
  providers: Array<{
    id: string
    label: string
    api_mode: 'openai_chat_completions' | 'anthropic_messages'
    base_url: string
    api_key_env: string
    models: Array<{
      id: string
      label: string
      context_window_tokens?: number
      output_reserve_tokens?: number
      reasoning?: CodingProviderReasoning
    }>
  }>
}

export type CloudModelApiMode =
  | 'openai_chat_completions'
  | 'openai_responses'
  | 'anthropic_messages'

export type CloudModelInput = {
  model_id: string
  display_name: string
  context_window_tokens: number | null
  output_reserve_tokens: number | null
  reasoning_supported: boolean
}

export type CloudModel = CloudModelInput & {
  id: string
  runtime_id: string
}

export type CloudModelProvider = {
  id: string
  name: string
  api_mode: CloudModelApiMode
  base_url: string
  key_configured: boolean
  key_hint: string
  status: 'untested' | 'connected' | 'error' | string
  last_tested_at: string | null
  models: CloudModel[]
}

export type CloudModelProvidersResponse = {
  providers: CloudModelProvider[]
  default_model: string | null
}

export type CloudModelProviderCreate = {
  name: string
  api_mode: CloudModelApiMode
  base_url: string
  api_key: string
  models: CloudModelInput[]
  default_model_id?: string | null
}

export type CloudModelProviderUpdate = Partial<
  Omit<CloudModelProviderCreate, 'default_model_id' | 'api_key'>
> & { api_key?: string }

export type CloudModelProviderTestResponse = {
  ok: boolean
  status: string
  tested_at: string
}

export type CloudModelDiscoveryResponse = { models: string[] }

export type CloudModelDefaultResponse = {
  provider_id: string
  model_id: string
  runtime_model_id: string
}

export type CodingUsageModelAggregate = {
  model: string
  input_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  total_tokens: number | null
}

export type CodingUsageDailyAggregate = {
  date: string
  input_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  total_tokens: number | null
}

export type CodingUsageSummary = {
  range_days: number
  request_count: number
  session_count: number
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  cache_read_tokens: number | null
  cache_creation_tokens: number | null
  cache_hit_ratio: number | null
  cost: number | null
  models: CodingUsageModelAggregate[]
  daily: CodingUsageDailyAggregate[]
}

export type CodingContextSnapshot = {
  model_id: string | null
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
  latest_attempt: Record<string, unknown> | null
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

export type MemoryCandidate = {
  content: string
  topic: string
  source: string
  source_ref: string
  created_at: string
}

export type MemoryProposal = {
  proposal_id: string
  workspace_id: string
  session_id: string
  run_id: string
  reflection_id: string
  status: 'pending' | 'approved' | 'rejected'
  projection_status: 'pending' | 'complete'
  revision: number
  base_revision: number
  candidate_count: number
  candidates: MemoryCandidate[]
  created_at: string
  updated_at: string
}

export type MemoryProposalsResponse = {
  proposals: MemoryProposal[]
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

export type CodingRunAuditStep = {
  tool: string
  status: string
  action_summary: string
  result_summary: string
  duration_ms: number
  arguments_preview: string
  result_preview: string
  arguments_truncated: boolean
  result_truncated: boolean
}

export type CodingRunAuditSummary = {
  run_id: string
  status: string
  headline: string
  tool_count: number
  completed_tool_count: number
  failed_tool_count: number
  approval_count: number
  duration_ms: number
  changed_files: string[]
  steps: CodingRunAuditStep[]
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
  audit?: CodingRunAuditSummary
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
  audit?: CodingRunAuditSummary
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
