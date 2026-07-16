export type ToolActivityViewModel = {
  tool: string
  args: Record<string, unknown>
  status: 'running' | 'done' | 'error'
  content: string
  durationMs?: number
}

export type ExecutionActivityViewModel = {
  kind: 'model' | 'tool' | 'approval' | 'retry'
  label: string
  detail?: string
  status: 'running' | 'done' | 'error'
}

export type ChatMessageViewModel = {
  id?: string
  run_id?: string
  role: 'user' | 'assistant'
  content: string
  tools?: ToolActivityViewModel[]
  activities?: ExecutionActivityViewModel[]
  isThinking?: boolean
}
