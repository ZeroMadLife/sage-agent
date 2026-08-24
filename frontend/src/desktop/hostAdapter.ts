import { invoke } from '@tauri-apps/api/core'

export type HostState = 'starting' | 'ready' | 'degraded' | 'blocked'
export type CapabilityState = 'ready' | 'degraded' | 'blocked'

interface DesktopSession {
  endpoint: string
  bearer: string
  instanceId: string
}

interface DesktopSessionLease {
  session: DesktopSession
  revision: number
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
let sessionRevision = 0
let hostStatusGeneration = 0
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
  const requestGeneration = ++hostStatusGeneration
  const expectedRevision = sessionRevision
  const snapshot = await invoke<DesktopHostSnapshot>('desktop_host_status')
  if (requestGeneration === hostStatusGeneration) {
    replaceSessionIfRevision(
      snapshot.state === 'ready' ? snapshot.session : null,
      expectedRevision,
    )
  }
  return snapshot
}

function sessionsEqual(left: DesktopSession | null, right: DesktopSession | null): boolean {
  if (left === null || right === null) return left === right
  return left.endpoint === right.endpoint
    && left.bearer === right.bearer
    && left.instanceId === right.instanceId
}

function replaceSession(next: DesktopSession | null): void {
  if (sessionsEqual(session, next)) {
    session = next
    return
  }
  session = next
  sessionRevision += 1
}

function replaceSessionIfRevision(next: DesktopSession | null, expectedRevision: number): boolean {
  if (sessionRevision !== expectedRevision) return false
  replaceSession(next)
  return true
}

function currentSessionLease(): DesktopSessionLease {
  if (!session) throw new Error('desktop_session_unavailable')
  return { session, revision: sessionRevision }
}

function currentSession(): DesktopSession {
  return currentSessionLease().session
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

async function recoverSession(
  expectedRevision: number,
  isActive: () => boolean = () => true,
): Promise<DesktopSessionLease | null> {
  if (!isActive()) return null
  if (sessionRevision !== expectedRevision) {
    return isActive() ? currentSessionLease() : null
  }
  const snapshot = await invoke<DesktopHostSnapshot>('desktop_host_status')
  if (!isActive()) return null
  publishConnectionState('degraded')
  const next = snapshot.state === 'ready' ? snapshot.session : null
  if (!replaceSessionIfRevision(next, expectedRevision)) {
    return isActive() ? currentSessionLease() : null
  }
  return currentSessionLease()
}

async function desktopFetchWithSession(
  path: string,
  init: RequestInit,
): Promise<{ response: Response, lease: DesktopSessionLease }> {
  const active = currentSessionLease()
  try {
    const response = await fetchWithSession(active.session, path, init)
    if (response.status !== 401 && response.status !== 403) {
      publishConnectionState('ready')
      return { response, lease: active }
    }
  } catch (error) {
    if (init.signal?.aborted) throw init.signal.reason ?? error
    // The bounded retry below refreshes the in-memory session after a transport failure.
  }
  const isActive = () => !init.signal?.aborted
  let refreshed: DesktopSessionLease | null
  try {
    refreshed = await recoverSession(active.revision, isActive)
  } catch (error) {
    if (init.signal?.aborted) throw init.signal.reason ?? error
    throw error
  }
  if (!refreshed || init.signal?.aborted) {
    throw init.signal?.reason ?? new Error('desktop_session_unavailable')
  }
  const response = await fetchWithSession(refreshed.session, path, init)
  if (response.status === 401 || response.status === 403) {
    replaceSessionIfRevision(null, refreshed.revision)
    publishConnectionState('degraded')
  } else {
    publishConnectionState('ready')
  }
  return { response, lease: refreshed }
}

export async function desktopFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return (await desktopFetchWithSession(path, init)).response
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
  const callerSignal = requestInit.signal
  if (callerSignal?.aborted) {
    throw callerSignal.reason ?? new DOMException('The operation was aborted', 'AbortError')
  }
  const initialResult = await desktopFetchWithSession(path, requestInit)
  const initial = initialResult.response
  if (callerSignal?.aborted) {
    await initial.body?.cancel(callerSignal.reason).catch(() => undefined)
    throw callerSignal.reason ?? new DOMException('The operation was aborted', 'AbortError')
  }
  if (!initial.ok || !initial.body) return initial

  let reader = initial.body.getReader()
  let activeLease = initialResult.lease
  let reconnectAttempts = 0
  let cancelled = false
  let generation = 0
  let cancellationReason: unknown
  let reconnectAbort: AbortController | null = null
  let outputController: ReadableStreamDefaultController<Uint8Array> | null = null
  let detachCallerAbort: (() => void) | null = null

  function isCurrent(expectedGeneration: number): boolean {
    return !cancelled && generation === expectedGeneration
  }

  function stopReconnectTransport(reason?: unknown): void {
    const abort = reconnectAbort
    reconnectAbort = null
    abort?.abort(reason)
  }

  async function cancelFlow(reason: unknown, errorOutput = false): Promise<void> {
    if (cancelled) return
    cancelled = true
    generation += 1
    cancellationReason = reason
    detachCallerAbort?.()
    detachCallerAbort = null
    stopReconnectTransport(reason)
    if (errorOutput) outputController?.error(reason)
    await reader.cancel(reason).catch(() => undefined)
  }

  async function reconnect(
    controller: ReadableStreamDefaultController<Uint8Array>,
    cause: unknown,
  ): Promise<boolean> {
    if (callerSignal?.aborted || cancelled) return false
    publishConnectionState('degraded')
    stopReconnectTransport(cause)
    if (reconnectAttempts >= 1) {
      replaceSessionIfRevision(null, activeLease.revision)
      detachCallerAbort?.()
      detachCallerAbort = null
      controller.error(cause)
      return false
    }
    reconnectAttempts += 1
    const expectedGeneration = generation
    await reader.cancel().catch(() => undefined)
    if (!isCurrent(expectedGeneration)) return false

    let refreshed: DesktopSessionLease | null = null
    try {
      refreshed = await recoverSession(activeLease.revision, () => isCurrent(expectedGeneration))
      if (!refreshed || !isCurrent(expectedGeneration)) return false
      const abort = new AbortController()
      reconnectAbort = abort
      const response = await fetchWithSession(refreshed.session, path, {
        ...requestInit,
        signal: abort.signal,
      })
      if (!isCurrent(expectedGeneration)) {
        await response.body?.cancel(cancellationReason).catch(() => undefined)
        return false
      }
      if (!response.ok || !response.body) {
        await response.body?.cancel().catch(() => undefined)
        throw new Error('desktop_sse_reconnect_failed')
      }
      const nextReader = response.body.getReader()
      if (!isCurrent(expectedGeneration)) {
        await nextReader.cancel(cancellationReason).catch(() => undefined)
        return false
      }
      reader = nextReader
      activeLease = refreshed
      publishConnectionState('ready')
      return true
    } catch (reconnectError) {
      if (!isCurrent(expectedGeneration)) return false
      stopReconnectTransport(reconnectError)
      replaceSessionIfRevision(null, refreshed?.revision ?? activeLease.revision)
      publishConnectionState('degraded')
      detachCallerAbort?.()
      detachCallerAbort = null
      controller.error(reconnectError)
      return false
    }
  }

  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      outputController = controller
    },
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
      await cancelFlow(reason)
    },
  })
  if (callerSignal) {
    const abortFlow = () => {
      const reason = callerSignal.reason ?? new DOMException('The operation was aborted', 'AbortError')
      void cancelFlow(reason, true)
    }
    callerSignal.addEventListener('abort', abortFlow, { once: true })
    detachCallerAbort = () => callerSignal.removeEventListener('abort', abortFlow)
    if (callerSignal.aborted) abortFlow()
  }
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
  hostStatusGeneration += 1
  replaceSession(null)
  await invoke('desktop_exit')
}

export async function desktopOpenDiagnostics(): Promise<void> {
  await invoke('desktop_open_diagnostics')
}
