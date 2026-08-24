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

export type OnboardingMode = 'local' | 'cloud'

export interface DesktopLocalProvider {
  provider_id: string
  name: string
  base_url: string
  key_ref: string
  key_hint: string
  key_configured: boolean
  status: string
  reason_code: string | null
  models: string[]
  default_model: string | null
  is_active: boolean
}

export interface DesktopOnboardingSnapshot {
  status: CapabilityState
  reason_code: string | null
  action: string | null
  stage: 'choose_mode' | 'cloud_unavailable' | 'select_workspace' | 'configure_provider' | 'complete' | 'blocked'
  mode: OnboardingMode | null
  workspace_name: string | null
  active_provider_id: string | null
  providers: DesktopLocalProvider[]
  capabilities: Record<string, DesktopCapability>
}

export type DesktopOnboardingAction =
  | { kind: 'choose_mode', mode: OnboardingMode }
  | { kind: 'select_workspace', workspace_path: string }
  | { kind: 'add_provider', name: string, base_url: string, api_key: string, default_model: string }
  | { kind: 'probe_provider', provider_id: string }
  | { kind: 'set_default_model', provider_id: string, model_id: string }
  | { kind: 'set_active_provider', provider_id: string }
  | { kind: 'rotate_provider_key', provider_id: string, api_key: string }
  | { kind: 'disconnect_provider', provider_id: string }
  | { kind: 'delete_provider', provider_id: string }
  | { kind: 'retry_provider_reconciliation' }

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

function currentSessionLeaseOrNull(): DesktopSessionLease | null {
  return session ? { session, revision: sessionRevision } : null
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
    const current = currentSessionLeaseOrNull()
    if (current) return isActive() ? current : null
    expectedRevision = sessionRevision
  }
  const snapshot = await invoke<DesktopHostSnapshot>('desktop_host_status')
  if (!isActive()) return null
  publishConnectionState('degraded')
  const next = snapshot.state === 'ready' ? snapshot.session : null
  if (!replaceSessionIfRevision(next, expectedRevision)) {
    return isActive() ? currentSessionLeaseOrNull() : null
  }
  return currentSessionLeaseOrNull()
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

function requestPath(input: RequestInfo | URL): string {
  const raw = input instanceof Request ? input.url : input.toString()
  const parsed = new URL(raw, window.location.origin)
  return `${parsed.pathname}${parsed.search}`
}

export function desktopAwareFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  return isDesktopRuntime() ? desktopFetch(requestPath(input), init) : fetch(input, init)
}

export async function desktopCapabilities(): Promise<DesktopCapabilities> {
  const response = await desktopFetch('/capabilities')
  if (!response.ok) throw new Error('desktop_capabilities_unavailable')
  return response.json() as Promise<DesktopCapabilities>
}

export async function desktopOnboardingStatus(): Promise<DesktopOnboardingSnapshot> {
  return invoke<DesktopOnboardingSnapshot>('desktop_onboarding_status')
}

export async function desktopOnboardingAction(
  action: DesktopOnboardingAction,
): Promise<DesktopOnboardingSnapshot> {
  return invoke<DesktopOnboardingSnapshot>('desktop_onboarding_action', { action })
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
  readonly connectionGeneration: number
  send(data: Parameters<WebSocket['send']>[0]): void
  close(code?: number, reason?: string): void
  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void
  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void
}

class ReconnectingDesktopWebSocket extends EventTarget implements DesktopWebSocketConnection {
  private socket: WebSocket | null = null
  private stopped = false
  private reconnectAttempts = 0
  private socketGeneration = 0
  private connectionEpoch = 0
  private sessionLeaseRevision = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private stableTimer: ReturnType<typeof setTimeout> | null = null
  private terminalClosed = false
  private readonly path: string

  constructor(path: string) {
    super()
    this.path = path
  }

  get readyState(): number {
    return this.socket?.readyState ?? WebSocket.CONNECTING
  }

  get connectionGeneration(): number {
    return this.socketGeneration
  }

  async connect(expectedEpoch = this.connectionEpoch, lease = currentSessionLease()): Promise<void> {
    if (this.stopped || expectedEpoch !== this.connectionEpoch) return
    const active = lease.session
    const url = endpointUrl(active, this.path).replace(/^http:/, 'ws:')
    const socket = new WebSocket(url, ['sage.v1', `sage-bearer.${active.bearer}`])
    if (this.stopped || expectedEpoch !== this.connectionEpoch) {
      socket.close()
      return
    }
    this.socketGeneration += 1
    this.sessionLeaseRevision = lease.revision
    this.socket = socket
    socket.addEventListener('open', (event) => {
      if (socket !== this.socket) return
      this.clearStableTimer()
      this.stableTimer = setTimeout(() => {
        if (!this.stopped && socket === this.socket && expectedEpoch === this.connectionEpoch) {
          this.reconnectAttempts = 0
        }
      }, 1000)
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
      this.clearStableTimer()
      if (this.stopped || event.wasClean) {
        this.dispatchTerminalClose(event.code, event.reason, event.wasClean, false)
        return
      }
      publishConnectionState('degraded')
      this.scheduleReconnect(event)
    })
  }

  send(data: Parameters<WebSocket['send']>[0]): void {
    if (!this.socket) throw new Error('desktop_socket_unavailable')
    this.socket.send(data)
  }

  close(code?: number, reason?: string): void {
    if (this.stopped) return
    this.stopped = true
    this.connectionEpoch += 1
    this.clearReconnectTimer()
    this.clearStableTimer()
    this.socket?.close(code, reason)
    this.dispatchTerminalClose(code ?? 1000, reason ?? '', true, false)
  }

  private scheduleReconnect(cause?: CloseEvent): void {
    if (this.stopped) return
    if (this.reconnectAttempts >= 3) {
      this.dispatchTerminalClose(
        cause?.code || 1013,
        cause?.reason || 'desktop_websocket_retry_exhausted',
        false,
        true,
      )
      return
    }
    const delay = 100 * 2 ** this.reconnectAttempts
    this.reconnectAttempts += 1
    const expectedEpoch = this.connectionEpoch
    this.clearReconnectTimer()
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      void this.reconnect(expectedEpoch)
    }, delay)
  }

  private async reconnect(expectedEpoch: number): Promise<void> {
    if (this.stopped || expectedEpoch !== this.connectionEpoch) return
    try {
      const refreshed = await recoverSession(
        this.sessionLeaseRevision,
        () => !this.stopped && expectedEpoch === this.connectionEpoch,
      )
      if (this.stopped || expectedEpoch !== this.connectionEpoch) return
      if (!refreshed) {
        this.scheduleReconnect()
        return
      }
      await this.connect(expectedEpoch, refreshed)
    } catch {
      if (this.stopped || expectedEpoch !== this.connectionEpoch) return
      publishConnectionState('degraded')
      this.scheduleReconnect()
    }
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
  }

  private clearStableTimer(): void {
    if (this.stableTimer !== null) clearTimeout(this.stableTimer)
    this.stableTimer = null
  }

  private dispatchTerminalClose(
    code: number,
    reason: string,
    wasClean: boolean,
    clearSession: boolean,
  ): void {
    if (this.terminalClosed) return
    this.terminalClosed = true
    this.clearReconnectTimer()
    this.clearStableTimer()
    if (clearSession) replaceSessionIfRevision(null, this.sessionLeaseRevision)
    if (clearSession) publishConnectionState('degraded')
    this.dispatchEvent(new CloseEvent('close', { code, reason, wasClean }))
  }
}

export async function desktopWebSocket(path: string): Promise<DesktopWebSocketConnection> {
  if (!path.startsWith('/')) throw new Error('desktop_path_rejected')
  const connection = new ReconnectingDesktopWebSocket(path)
  await connection.connect()
  return connection
}

export interface DesktopSocketLike {
  readonly readyState: number
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onerror: (() => void) | null
  onclose: ((event?: { code?: number, wasClean?: boolean }) => void) | null
  send(data: string): void
  close(): void
}

class DeferredDesktopSocket implements DesktopSocketLike {
  private connection: DesktopWebSocketConnection | null = null
  private stopped = false
  private openedGeneration = 0
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event?: { code?: number, wasClean?: boolean }) => void) | null = null

  constructor(url: string) {
    const parsed = new URL(url, window.location.origin)
    void this.connect(`${parsed.pathname}${parsed.search}`)
  }

  get readyState(): number {
    return this.connection?.readyState ?? WebSocket.CONNECTING
  }

  send(data: string): void {
    if (!this.connection) throw new Error('desktop_socket_unavailable')
    this.connection.send(data)
  }

  close(): void {
    this.stopped = true
    this.connection?.close()
  }

  private async connect(path: string): Promise<void> {
    try {
      const connection = await desktopWebSocket(path)
      if (this.stopped) {
        connection.close()
        return
      }
      this.connection = connection
      const reportOpen = () => {
        if (connection.connectionGeneration <= this.openedGeneration) return
        this.openedGeneration = connection.connectionGeneration
        this.onopen?.()
      }
      connection.addEventListener('open', reportOpen)
      connection.addEventListener('message', (event) => {
        this.onmessage?.({ data: String((event as MessageEvent).data) })
      })
      connection.addEventListener('error', () => this.onerror?.())
      connection.addEventListener('close', (event) => {
        const close = event as CloseEvent
        this.onclose?.({ code: close.code, wasClean: close.wasClean })
      })
      if (connection.readyState === WebSocket.OPEN) reportOpen()
    } catch {
      if (!this.stopped) {
        this.onerror?.()
        this.onclose?.({ code: 1006, wasClean: false })
      }
    }
  }
}

export function desktopSocket(url: string): DesktopSocketLike {
  return new DeferredDesktopSocket(url)
}

export async function desktopExit(): Promise<void> {
  hostStatusGeneration += 1
  replaceSession(null)
  await invoke('desktop_exit')
}

export async function desktopOpenDiagnostics(): Promise<void> {
  await invoke('desktop_open_diagnostics')
}
