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

function endpointUrl(path: string): string {
  if (!path.startsWith('/')) throw new Error('desktop_path_rejected')
  return `${currentSession().endpoint}${path}`
}

export async function desktopFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const active = currentSession()
  const existing = Object.fromEntries(new Headers(init.headers).entries())
  return fetch(endpointUrl(path), {
    ...init,
    headers: {
      ...existing,
      Authorization: `Bearer ${active.bearer}`,
    },
  })
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

export function desktopWebSocket(path: string): WebSocket {
  const active = currentSession()
  const url = endpointUrl(path).replace(/^http:/, 'ws:')
  return new WebSocket(url, ['sage.v1', `sage-bearer.${active.bearer}`])
}

export async function desktopExit(): Promise<void> {
  session = null
  await invoke('desktop_exit')
}
