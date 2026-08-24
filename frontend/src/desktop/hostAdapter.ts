import { invoke } from '@tauri-apps/api/core'

export type HostState = 'starting' | 'ready' | 'degraded' | 'blocked'
export type CapabilityState = 'ready' | 'degraded' | 'blocked'

interface DesktopSession {
  endpoint: string
  bearer: string
  instanceId: string
}

export interface DesktopHostSnapshot {
  state: HostState
  reasonCode: string | null
  action: string | null
  session: DesktopSession | null
}

export interface DesktopCapability {
  status: CapabilityState
  reason_code: string | null
  action: string | null
}

export interface DesktopCapabilities {
  status: CapabilityState
  api_version: string
  build_sha: string
  capabilities: Record<string, DesktopCapability>
}

let session: DesktopSession | null = null
type DesktopConnectionState = 'ready' | 'degraded'
type DesktopConnectionListener = (state: DesktopConnectionState) => void
const connectionListeners = new Set<DesktopConnectionListener>()

function publishConnectionState(state: DesktopConnectionState): void {
  for (const listener of connectionListeners) listener(state)
}

export function onDesktopConnectionState(listener: DesktopConnectionListener): () => void {
  connectionListeners.add(listener)
  return () => connectionListeners.delete(listener)
}

export function isDesktopRuntime(): boolean {
  return '__TAURI_INTERNALS__' in globalThis
}

export async function desktopHostStatus(): Promise<DesktopHostSnapshot> {
  const snapshot = await invoke<DesktopHostSnapshot>('desktop_host_status')
  session = snapshot.state === 'ready' ? snapshot.session : null
  return snapshot
}

function currentSession(): DesktopSession {
  if (!session) throw new Error('desktop_session_unavailable')
  return session
}

function endpointUrl(active: DesktopSession, path: string): string {
  if (!path.startsWith('/')) throw new Error('desktop_path_rejected')
  return `${active.endpoint}${path}`
}

async function fetchWithSession(
  active: DesktopSession,
  path: string,
  init: RequestInit,
): Promise<Response> {
  const existing = Object.fromEntries(new Headers(init.headers).entries())
  return fetch(endpointUrl(active, path), {
    ...init,
    headers: {
      ...existing,
      Authorization: `Bearer ${active.bearer}`,
    },
  })
}

async function recoverSession(): Promise<DesktopSession> {
  session = null
  publishConnectionState('degraded')
  await desktopHostStatus()
  return currentSession()
}

export async function desktopFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const active = currentSession()
  try {
    const response = await fetchWithSession(active, path, init)
    if (response.status !== 401 && response.status !== 403) {
      publishConnectionState('ready')
      return response
    }
  } catch {
    // The bounded retry below refreshes the in-memory session after a transport failure.
  }
  const refreshed = await recoverSession()
  const response = await fetchWithSession(refreshed, path, init)
  if (response.status === 401 || response.status === 403) {
    session = null
    publishConnectionState('degraded')
  } else {
    publishConnectionState('ready')
  }
  return response
}

export async function desktopCapabilities(): Promise<DesktopCapabilities> {
  const response = await desktopFetch('/capabilities')
  if (!response.ok) throw new Error('desktop_capabilities_unavailable')
  return response.json() as Promise<DesktopCapabilities>
}

function sseRequestInit(init: RequestInit): RequestInit {
  return {
    ...init,
    headers: { ...Object.fromEntries(new Headers(init.headers).entries()), Accept: 'text/event-stream' },
  }
}

export async function desktopSse(path: string, init: RequestInit = {}): Promise<Response> {
  const requestInit = sseRequestInit(init)
  const initial = await desktopFetch(path, requestInit)
  if (!initial.ok || !initial.body) return initial

  let reader = initial.body.getReader()
  let reconnectAttempts = 0
  let cancelled = false
  let generation = 0
  let cancellationReason: unknown
  let reconnectAbort: AbortController | null = null
  let detachCallerAbort: (() => void) | null = null

  function isCurrent(expectedGeneration: number): boolean {
    return !cancelled && generation === expectedGeneration
  }

  function stopReconnectTransport(reason?: unknown): void {
    const abort = reconnectAbort
    reconnectAbort = null
    detachCallerAbort?.()
    detachCallerAbort = null
    abort?.abort(reason)
  }

  async function reconnect(
    controller: ReadableStreamDefaultController<Uint8Array>,
    cause: unknown,
  ): Promise<boolean> {
    publishConnectionState('degraded')
    stopReconnectTransport(cause)
    if (reconnectAttempts >= 1) {
      session = null
      controller.error(cause)
      return false
    }
    reconnectAttempts += 1
    const expectedGeneration = generation
    await reader.cancel().catch(() => undefined)
    if (!isCurrent(expectedGeneration)) return false

    try {
      const refreshed = await recoverSession()
      if (!isCurrent(expectedGeneration)) {
        session = null
        return false
      }
      const abort = new AbortController()
      reconnectAbort = abort
      const callerSignal = requestInit.signal
      if (callerSignal) {
        const forwardAbort = () => abort.abort(callerSignal.reason)
        if (callerSignal.aborted) forwardAbort()
        else {
          callerSignal.addEventListener('abort', forwardAbort, { once: true })
          detachCallerAbort = () => callerSignal.removeEventListener('abort', forwardAbort)
        }
      }
      const response = await fetchWithSession(refreshed, path, {
        ...requestInit,
        signal: abort.signal,
      })
      if (!isCurrent(expectedGeneration)) {
        session = null
        await response.body?.cancel(cancellationReason).catch(() => undefined)
        return false
      }
      if (!response.ok || !response.body) {
        await response.body?.cancel().catch(() => undefined)
        throw new Error('desktop_sse_reconnect_failed')
      }
      const nextReader = response.body.getReader()
      if (!isCurrent(expectedGeneration)) {
        session = null
        await nextReader.cancel(cancellationReason).catch(() => undefined)
        return false
      }
      reader = nextReader
      publishConnectionState('ready')
      return true
    } catch (reconnectError) {
      if (!isCurrent(expectedGeneration)) {
        session = null
        return false
      }
      stopReconnectTransport(reconnectError)
      session = null
      publishConnectionState('degraded')
      controller.error(reconnectError)
      return false
    }
  }

  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      while (!cancelled) {
        let chunk: ReadableStreamReadResult<Uint8Array>
        try {
          chunk = await reader.read()
        } catch (error) {
          if (cancelled || !(await reconnect(controller, error))) return
          continue
        }
        if (cancelled) return
        if (chunk.done) {
          if (!(await reconnect(controller, new Error('desktop_sse_stream_ended')))) return
          continue
        }
        controller.enqueue(chunk.value)
        return
      }
    },
    async cancel(reason) {
      cancelled = true
      generation += 1
      cancellationReason = reason
      stopReconnectTransport(reason)
      await reader.cancel(reason).catch(() => undefined)
    },
  })
  return new Response(body, {
    headers: new Headers(initial.headers),
    status: initial.status,
    statusText: initial.statusText,
  })
}

export interface DesktopWebSocketConnection {
  readonly readyState: number
  send(data: Parameters<WebSocket['send']>[0]): void
  close(code?: number, reason?: string): void
  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void
  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void
}

class ReconnectingDesktopWebSocket extends EventTarget implements DesktopWebSocketConnection {
  private socket: WebSocket | null = null
  private stopped = false
  private reconnectAttempts = 0
  private readonly path: string

  constructor(path: string) {
    super()
    this.path = path
  }

  get readyState(): number {
    return this.socket?.readyState ?? WebSocket.CONNECTING
  }

  async connect(): Promise<void> {
    const active = currentSession()
    const url = endpointUrl(active, this.path).replace(/^http:/, 'ws:')
    const socket = new WebSocket(url, ['sage.v1', `sage-bearer.${active.bearer}`])
    this.socket = socket
    socket.addEventListener('open', (event) => {
      if (socket !== this.socket) return
      this.reconnectAttempts = 0
      publishConnectionState('ready')
      this.dispatchEvent(new Event(event.type))
    })
    socket.addEventListener('message', (event) => {
      if (socket === this.socket) {
        this.dispatchEvent(new MessageEvent(event.type, { data: event.data, origin: event.origin }))
      }
    })
    socket.addEventListener('error', (event) => {
      if (socket === this.socket) this.dispatchEvent(new Event(event.type))
    })
    socket.addEventListener('close', (event) => {
      if (socket !== this.socket) return
      if (this.stopped || event.wasClean) {
        this.dispatchEvent(new CloseEvent(event.type, {
          code: event.code,
          reason: event.reason,
          wasClean: event.wasClean,
        }))
        return
      }
      publishConnectionState('degraded')
      this.scheduleReconnect()
    })
  }

  send(data: Parameters<WebSocket['send']>[0]): void {
    if (!this.socket) throw new Error('desktop_socket_unavailable')
    this.socket.send(data)
  }

  close(code?: number, reason?: string): void {
    this.stopped = true
    this.socket?.close(code, reason)
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectAttempts >= 3) return
    const delay = 100 * 2 ** this.reconnectAttempts
    this.reconnectAttempts += 1
    setTimeout(() => void this.reconnect(), delay)
  }

  private async reconnect(): Promise<void> {
    if (this.stopped) return
    try {
      await desktopHostStatus()
      await this.connect()
    } catch {
      publishConnectionState('degraded')
      this.scheduleReconnect()
    }
  }
}

export async function desktopWebSocket(path: string): Promise<DesktopWebSocketConnection> {
  if (!path.startsWith('/')) throw new Error('desktop_path_rejected')
  const connection = new ReconnectingDesktopWebSocket(path)
  await connection.connect()
  return connection
}

export async function desktopExit(): Promise<void> {
  session = null
  await invoke('desktop_exit')
}

export async function desktopOpenDiagnostics(): Promise<void> {
  await invoke('desktop_open_diagnostics')
}
