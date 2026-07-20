import type { CodingServerEvent, CodingTimelineEvent } from '../types/api'
import { serializeHarnessSurfaceContext } from '../harness/surfaceContext'
import type { HarnessSurfaceContext } from '../harness/types'

export type WebSocketLike = {
  readyState: number
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onerror: (() => void) | null
  onclose: ((event?: { code?: number; wasClean?: boolean }) => void) | null
  send(data: string): void
  close(): void
}

export type WebSocketFactory = (url: string) => WebSocketLike

export type CodingStreamOptions = {
  createSocket?: WebSocketFactory
  onEvent: (sessionId: string, event: CodingTimelineEvent) => void
  onRawEvent?: (event: CodingServerEvent) => void
  onOpen?: (sessionId: string) => void
  onConnectionState?: (sessionId: string, state: CodingConnectionState) => void
  onError: (message: string) => void
}

export type CodingConnectionState =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'recovering'
  | 'disconnected'

const OPEN = 1
const MAX_SEEN_EVENT_IDS = 2_048
const TIMELINE_KINDS = new Set([
  'user', 'assistant', 'model', 'tool', 'approval', 'context', 'memory', 'proposal',
  'agent', 'terminal', 'system', 'run', 'harness',
])
const TIMELINE_STATUSES = new Set([
  'pending', 'queued', 'running', 'blocked', 'done', 'completed', 'cancelled',
  'error', 'interrupted', 'retryable',
])

export class CodingStream {
  private socket: WebSocketLike | null = null
  private generation = 0
  private sessionId = ''
  private url = ''
  private cursor = 0
  private reconnectAttempt = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectEnabled = false
  private readonly seenEventIds = new Set<string>()
  private readonly seenEventOrder: string[] = []
  private readonly createSocket: WebSocketFactory
  private readonly onEvent: (sessionId: string, event: CodingTimelineEvent) => void
  private readonly onRawEvent: ((event: CodingServerEvent) => void) | null
  private readonly onOpen: ((sessionId: string) => void) | null
  private readonly onConnectionState: (
    (sessionId: string, state: CodingConnectionState) => void
  ) | null
  private readonly onError: (message: string) => void

  constructor(options: CodingStreamOptions) {
    this.createSocket =
      options.createSocket ||
      ((url: string) => new WebSocket(url) as unknown as WebSocketLike)
    this.onEvent = options.onEvent
    this.onRawEvent = options.onRawEvent ?? null
    this.onOpen = options.onOpen ?? null
    this.onConnectionState = options.onConnectionState ?? null
    this.onError = options.onError
  }

  connect(sessionId: string, url: string): void {
    this.disconnect()
    this.sessionId = sessionId
    this.url = url
    this.cursor = cursorFromUrl(url)
    this.reconnectAttempt = 0
    this.reconnectEnabled = true
    this.seenEventIds.clear()
    this.seenEventOrder.length = 0
    this.onConnectionState?.(sessionId, 'connecting')
    this.openSocket()
  }

  private openSocket(): void {
    const generation = ++this.generation
    const sessionId = this.sessionId
    const url = withCursor(this.url, this.cursor)
    const socket = this.createSocket(url)
    this.socket = socket
    socket.onopen = () => {
      if (generation !== this.generation || this.socket !== socket) return
      this.reconnectAttempt = 0
      this.onConnectionState?.(sessionId, 'connected')
      this.onOpen?.(sessionId)
    }
    socket.onmessage = (event) => {
      if (generation !== this.generation) return
      let candidate: unknown
      try {
        candidate = JSON.parse(event.data)
      } catch {
        this.onError('无法解析运行事件')
        return
      }
      if (!isTimelineEvent(candidate)) {
        // Fallback: if the backend sends raw events (no timeline envelope),
        // forward to onRawEvent instead of erroring. This keeps the workbench
        // functional before the timeline backend contract is implemented.
        if (this.onRawEvent && isRawEvent(candidate)) {
          this.onRawEvent(candidate as CodingServerEvent)
        }
        return
      }
      const envelope = candidate
      if (envelope.session_id !== sessionId || envelope.sequence <= this.cursor ||
        this.seenEventIds.has(envelope.event_id)) return
      this.rememberEventId(envelope.event_id)
      this.cursor = Math.max(this.cursor, envelope.sequence)
      this.reconnectAttempt = 0
      this.onEvent(sessionId, envelope)
    }
    socket.onerror = () => {
      if (generation !== this.generation) return
      this.onConnectionState?.(sessionId, 'recovering')
      this.onError('连接中断')
    }
    socket.onclose = (event) => {
      if (generation !== this.generation) return
      if (this.socket === socket) this.socket = null
      if (!this.reconnectEnabled) return
      const code = event?.code ?? 1006
      if (code === 1000 || code === 1008 || (event?.wasClean && code < 4000)) {
        this.onConnectionState?.(sessionId, 'disconnected')
        return
      }
      this.onConnectionState?.(sessionId, 'recovering')
      const delay = Math.min(8_000, 500 * 2 ** this.reconnectAttempt)
      this.reconnectAttempt += 1
      this.reconnectTimer = setTimeout(() => {
        if (generation !== this.generation || !this.reconnectEnabled) return
        this.openSocket()
      }, delay)
    }
  }

  private rememberEventId(eventId: string): void {
    this.seenEventIds.add(eventId)
    this.seenEventOrder.push(eventId)
    if (this.seenEventOrder.length <= MAX_SEEN_EVENT_IDS) return
    const expired = this.seenEventOrder.shift()
    if (expired) this.seenEventIds.delete(expired)
  }

  send(content: string, surfaceContext?: HarnessSurfaceContext | null): boolean {
    if (!this.socket || this.socket.readyState !== OPEN) return false
    this.socket.send(JSON.stringify({
      content,
      ...(surfaceContext
        ? { surface_context: serializeHarnessSurfaceContext(surfaceContext) }
        : {}),
    }))
    return true
  }

  stop(): void {
    this.disconnect()
  }

  disconnect(): void {
    this.reconnectEnabled = false
    this.generation += 1
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    const socket = this.socket
    this.socket = null
    socket?.close()
    if (this.sessionId) this.onConnectionState?.(this.sessionId, 'disconnected')
  }
}

function isTimelineEvent(value: unknown): value is CodingTimelineEvent {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const item = value as Record<string, unknown>
  return typeof item.event_id === 'string' && item.event_id.length > 0 &&
    typeof item.session_id === 'string' && item.session_id.length > 0 &&
    typeof item.run_id === 'string' && item.run_id.length > 0 &&
    Number.isSafeInteger(item.sequence) && Number(item.sequence) > 0 &&
    typeof item.kind === 'string' && TIMELINE_KINDS.has(item.kind) &&
    typeof item.status === 'string' && TIMELINE_STATUSES.has(item.status) &&
    typeof item.timestamp === 'string' && item.timestamp.length > 0 &&
    Boolean(item.payload) && typeof item.payload === 'object' && !Array.isArray(item.payload)
}

function isRawEvent(value: unknown): value is CodingServerEvent {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const item = value as Record<string, unknown>
  return typeof item.type === 'string' && item.type.length > 0
}

function cursorFromUrl(url: string): number {
  const parsed = Number(new URL(url, window.location.origin).searchParams.get('after') || 0)
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : 0
}

function withCursor(url: string, cursor: number): string {
  const parsed = new URL(url, window.location.origin)
  parsed.searchParams.set('after', String(cursor))
  return parsed.toString()
}
