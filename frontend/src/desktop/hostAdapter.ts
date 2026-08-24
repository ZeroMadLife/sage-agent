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

export function desktopSse(path: string, init: RequestInit = {}): Promise<Response> {
  return desktopFetch(path, {
    ...init,
    headers: { ...Object.fromEntries(new Headers(init.headers).entries()), Accept: 'text/event-stream' },
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
