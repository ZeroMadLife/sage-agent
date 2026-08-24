import { beforeEach, describe, expect, it, vi } from 'vitest'

const invoke = vi.fn()
vi.mock('@tauri-apps/api/core', () => ({ invoke }))

describe('DesktopHostAdapter', () => {
  beforeEach(() => {
    vi.resetModules()
    invoke.mockReset()
    localStorage.clear()
    vi.stubGlobal('__TAURI_INTERNALS__', {})
  })

  it('keeps the startup bearer in memory and out of storage and URLs', async () => {
    invoke.mockResolvedValue({
      state: 'ready',
      reasonCode: null,
      action: null,
      session: {
        endpoint: 'http://127.0.0.1:49152',
        bearer: 'memory-only-token',
        instanceId: 'instance',
      },
    })
    const storage = vi.spyOn(Storage.prototype, 'setItem')
    const { desktopHostStatus, desktopFetch } = await import('./hostAdapter')
    await desktopHostStatus()
    const fetch = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetch)

    await desktopFetch('/capabilities')

    expect(fetch).toHaveBeenCalledWith(
      'http://127.0.0.1:49152/capabilities',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer memory-only-token' }),
      }),
    )
    expect(fetch.mock.calls[0][0]).not.toContain('memory-only-token')
    expect(storage).not.toHaveBeenCalled()
    expect(JSON.stringify(localStorage)).not.toContain('memory-only-token')
  })

  it('uses fetch-based SSE and a header subprotocol for WebSocket', async () => {
    invoke.mockResolvedValue({
      state: 'ready', reasonCode: null, action: null,
      session: { endpoint: 'http://127.0.0.1:49152', bearer: 'token', instanceId: 'i' },
    })
    const { desktopHostStatus, desktopSse, desktopWebSocket } = await import('./hostAdapter')
    await desktopHostStatus()
    const fetch = vi.fn().mockResolvedValue(new Response('event: ready\n\n'))
    vi.stubGlobal('fetch', fetch)
    const websocket = vi.fn(function WebSocketMock(_url: string, _protocols: string[]) {
      return { addEventListener: vi.fn(), close: vi.fn(), send: vi.fn() }
    })
    vi.stubGlobal('WebSocket', websocket)

    await desktopSse('/desktop/probe/sse')
    await desktopWebSocket('/desktop/probe/ws')

    expect(fetch).toHaveBeenCalledWith(
      'http://127.0.0.1:49152/desktop/probe/sse',
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer token' }) }),
    )
    expect(websocket).toHaveBeenCalledWith(
      'ws://127.0.0.1:49152/desktop/probe/ws',
      ['sage.v1', 'sage-bearer.token'],
    )
    expect(websocket.mock.calls[0][0]).not.toContain('token')
  })

  it('refreshes the host session once when an SSE body fails mid-stream', async () => {
    invoke
      .mockResolvedValueOnce({
        state: 'ready', reasonCode: null, action: null,
        session: { endpoint: 'http://127.0.0.1:49152', bearer: 'old', instanceId: 'old' },
      })
      .mockResolvedValueOnce({
        state: 'ready', reasonCode: null, action: null,
        session: { endpoint: 'http://127.0.0.1:49153', bearer: 'new', instanceId: 'new' },
      })
    const encoder = new TextEncoder()
    const broken = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: old\n\n'))
      },
      pull(controller) {
        controller.error(new TypeError('connection reset'))
      },
    })
    const recovered = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: new\n\n'))
        controller.close()
      },
    })
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(broken, { headers: { 'Content-Type': 'text/event-stream' } }))
      .mockResolvedValueOnce(new Response(recovered, { headers: { 'Content-Type': 'text/event-stream' } }))
    vi.stubGlobal('fetch', fetch)
    const { desktopHostStatus, desktopSse, onDesktopConnectionState } = await import('./hostAdapter')
    await desktopHostStatus()
    const states: string[] = []
    onDesktopConnectionState((state) => states.push(state))

    const response = await desktopSse('/desktop/probe/sse')

    await expect(response.text()).resolves.toBe('event: old\n\nevent: new\n\n')
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      'http://127.0.0.1:49153/desktop/probe/sse',
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer new' }) }),
    )
    expect(invoke).toHaveBeenCalledTimes(2)
    expect(states).toContain('degraded')
    expect(states.at(-1)).toBe('ready')
  })

  it('fails closed after the bounded SSE body reconnect is exhausted', async () => {
    invoke
      .mockResolvedValueOnce({
        state: 'ready', reasonCode: null, action: null,
        session: { endpoint: 'http://127.0.0.1:49152', bearer: 'old', instanceId: 'old' },
      })
      .mockResolvedValueOnce({
        state: 'ready', reasonCode: null, action: null,
        session: { endpoint: 'http://127.0.0.1:49153', bearer: 'new', instanceId: 'new' },
      })
    const brokenResponse = () => new Response(new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.error(new TypeError('connection reset'))
      },
    }), { headers: { 'Content-Type': 'text/event-stream' } })
    const fetch = vi.fn()
      .mockResolvedValueOnce(brokenResponse())
      .mockResolvedValueOnce(brokenResponse())
    vi.stubGlobal('fetch', fetch)
    const { desktopHostStatus, desktopSse, onDesktopConnectionState } = await import('./hostAdapter')
    await desktopHostStatus()
    const states: string[] = []
    onDesktopConnectionState((state) => states.push(state))

    const response = await desktopSse('/desktop/probe/sse')

    await expect(response.text()).rejects.toThrow('connection reset')
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(invoke).toHaveBeenCalledTimes(2)
    expect(states.at(-1)).toBe('degraded')
  })

  it('forwards SSE reader cancellation to the active transport', async () => {
    invoke.mockResolvedValue({
      state: 'ready', reasonCode: null, action: null,
      session: { endpoint: 'http://127.0.0.1:49152', bearer: 'token', instanceId: 'i' },
    })
    const cancel = vi.fn()
    const stream = new ReadableStream<Uint8Array>({
      pull() {
        return new Promise(() => undefined)
      },
      cancel,
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream)))
    const { desktopHostStatus, desktopSse } = await import('./hostAdapter')
    await desktopHostStatus()
    const response = await desktopSse('/desktop/probe/sse')

    await response.body?.getReader().cancel('consumer stopped')

    expect(cancel).toHaveBeenCalledWith('consumer stopped')
  })

  it('refreshes a rotated host session once after an authorization failure', async () => {
    invoke
      .mockResolvedValueOnce({
        state: 'ready', reasonCode: null, action: null,
        session: { endpoint: 'http://127.0.0.1:49152', bearer: 'old', instanceId: 'old' },
      })
      .mockResolvedValueOnce({
        state: 'ready', reasonCode: null, action: null,
        session: { endpoint: 'http://127.0.0.1:49153', bearer: 'new', instanceId: 'new' },
      })
    const { desktopHostStatus, desktopFetch } = await import('./hostAdapter')
    await desktopHostStatus()
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response('{}', { status: 401 }))
      .mockResolvedValueOnce(new Response('{"status":"ok"}', { status: 200 }))
    vi.stubGlobal('fetch', fetch)

    const response = await desktopFetch('/capabilities')

    expect(response.status).toBe(200)
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      'http://127.0.0.1:49153/capabilities',
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer new' }) }),
    )
    expect(invoke).toHaveBeenCalledTimes(2)
  })

  it('reports connection loss and bounds host-session recovery', async () => {
    invoke
      .mockResolvedValueOnce({
        state: 'ready', reasonCode: null, action: null,
        session: { endpoint: 'http://127.0.0.1:49152', bearer: 'old', instanceId: 'old' },
      })
      .mockResolvedValueOnce({ state: 'degraded', reasonCode: 'desktop_sidecar_crashed', action: 'wait_for_restart', session: null })
    const { desktopHostStatus, desktopFetch, onDesktopConnectionState } = await import('./hostAdapter')
    await desktopHostStatus()
    const states: string[] = []
    onDesktopConnectionState((state) => states.push(state))
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('connection reset')))

    await expect(desktopFetch('/capabilities')).rejects.toThrow('desktop_session_unavailable')

    expect(invoke).toHaveBeenCalledTimes(2)
    expect(states).toContain('degraded')
  })

  it('opens the fixed host diagnostics command without exposing a path', async () => {
    invoke.mockResolvedValue(undefined)
    const { desktopOpenDiagnostics } = await import('./hostAdapter')

    await desktopOpenDiagnostics()

    expect(invoke).toHaveBeenCalledWith('desktop_open_diagnostics')
  })

  it('reconnects an abnormal WebSocket close with the rotated host session', async () => {
    vi.useFakeTimers()
    invoke
      .mockResolvedValueOnce({
        state: 'ready', reasonCode: null, action: null,
        session: { endpoint: 'http://127.0.0.1:49152', bearer: 'old', instanceId: 'old' },
      })
      .mockResolvedValueOnce({
        state: 'ready', reasonCode: null, action: null,
        session: { endpoint: 'http://127.0.0.1:49153', bearer: 'new', instanceId: 'new' },
      })
    const sockets: Array<{ emit: (type: string, event: Event) => void }> = []
    const websocket = vi.fn(function WebSocketMock(_url: string, _protocols: string[]) {
      const listeners = new Map<string, Array<(event: Event) => void>>()
      const socket = {
        readyState: 1,
        addEventListener(type: string, listener: (event: Event) => void) {
          listeners.set(type, [...(listeners.get(type) ?? []), listener])
        },
        emit(type: string, event: Event) {
          for (const listener of listeners.get(type) ?? []) listener(event)
        },
        close: vi.fn(),
        send: vi.fn(),
      }
      sockets.push(socket)
      return socket
    })
    vi.stubGlobal('WebSocket', websocket)
    const { desktopHostStatus, desktopWebSocket } = await import('./hostAdapter')
    await desktopHostStatus()
    const connection = await desktopWebSocket('/desktop/probe/ws')

    sockets[0].emit('close', new CloseEvent('close', { wasClean: false }))
    await vi.advanceTimersByTimeAsync(100)

    expect(websocket).toHaveBeenCalledTimes(2)
    expect(websocket).toHaveBeenLastCalledWith(
      'ws://127.0.0.1:49153/desktop/probe/ws',
      ['sage.v1', 'sage-bearer.new'],
    )
    connection.close()
    vi.useRealTimers()
  })
})
