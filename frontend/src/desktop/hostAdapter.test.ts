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
    const websocket = vi.fn()
    vi.stubGlobal('WebSocket', websocket)

    await desktopSse('/desktop/probe/sse')
    desktopWebSocket('/desktop/probe/ws')

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
})
