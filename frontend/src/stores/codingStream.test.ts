import { describe, expect, it, vi } from 'vitest'
import { CodingStream, type WebSocketLike } from './codingStream'
import type { CodingTimelineEvent } from '../types/api'

class FakeSocket implements WebSocketLike {
  readyState = 1
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event?: { code?: number; wasClean?: boolean }) => void) | null = null
  sent: string[] = []
  closed = false

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    this.closed = true
    this.readyState = 3
    this.onclose?.()
  }

  emit(event: CodingTimelineEvent): void {
    this.onmessage?.({ data: JSON.stringify(event) })
  }

  emitOpen(): void {
    this.readyState = 1
    this.onopen?.()
  }

  emitRaw(data: string): void {
    this.onmessage?.({ data })
  }
}

describe('CodingStream', () => {
  function envelope(sequence: number, sessionId = 'coding_1'): CodingTimelineEvent {
    return {
      event_id: `event-${sequence}`,
      session_id: sessionId,
      run_id: 'run-1',
      sequence,
      kind: 'assistant',
      status: 'completed',
      timestamp: '2026-07-12T00:00:00Z',
      payload: { type: 'final', content: 'done' },
    }
  }

  it('connects and sends user messages', () => {
    const sockets: FakeSocket[] = []
    const onEvent = vi.fn()
    const stream = new CodingStream({
      createSocket: () => {
        const socket = new FakeSocket()
        sockets.push(socket)
        return socket
      },
      onEvent,
      onError: vi.fn(),
    })

    stream.connect('coding_1', 'ws://local/stream')
    const sent = stream.send('hello')
    sockets[0].emit(envelope(1))

    expect(sent).toBe(true)
    expect(sockets[0].sent).toEqual([JSON.stringify({ content: 'hello' })])
    expect(onEvent).toHaveBeenCalledWith('coding_1', envelope(1))
  })

  it('freezes a surface context into the websocket message payload', () => {
    const socket = new FakeSocket()
    const stream = new CodingStream({
      createSocket: () => socket,
      onEvent: vi.fn(),
      onError: vi.fn(),
    })
    const context = {
      surface: 'knowledge' as const,
      workspaceId: 'knowledge-local',
      resource: { type: 'knowledge_page' as const, id: 'page-1', revision: 'rev-1' },
      selection: { type: 'graph_node' as const, id: 'node-1', revision: 'rev-1' },
      graphRevision: 'graph-1',
      operationRefs: [{ kind: 'knowledge_job' as const, id: 'job-1' }],
    }

    stream.connect('coding_1', 'ws://local/stream')
    expect(stream.send('解释当前节点', context)).toBe(true)
    context.selection.id = 'node-changed-after-send'

    expect(JSON.parse(socket.sent[0])).toEqual({
      content: '解释当前节点',
      surface_context: {
        surface: 'knowledge',
        workspace_id: 'knowledge-local',
        resource: { type: 'knowledge_page', id: 'page-1', revision: 'rev-1' },
        selection: { type: 'graph_node', id: 'node-1', revision: 'rev-1' },
        graph_revision: 'graph-1',
        operation_refs: [{ kind: 'knowledge_job', id: 'job-1' }],
      },
    })
  })

  it('forwards explicit Harness stage events to the timeline store', () => {
    const socket = new FakeSocket()
    const onEvent = vi.fn()
    const stream = new CodingStream({
      createSocket: () => socket,
      onEvent,
      onError: vi.fn(),
    })
    const event: CodingTimelineEvent = {
      ...envelope(1),
      kind: 'harness',
      status: 'running',
      payload: {
        type: 'stage_started',
        definition_id: 'sage.coding.practice',
        definition_version: 1,
        stage_id: 'plan',
      },
    }

    stream.connect('coding_1', 'ws://local/stream')
    socket.emit(event)

    expect(onEvent).toHaveBeenCalledWith('coding_1', event)
  })

  it('disconnect closes the active socket and prevents sending', () => {
    const sockets: FakeSocket[] = []
    const stream = new CodingStream({
      createSocket: () => {
        const socket = new FakeSocket()
        sockets.push(socket)
        return socket
      },
      onEvent: vi.fn(),
      onError: vi.fn(),
    })

    stream.connect('coding_1', 'ws://local/stream')
    stream.disconnect()

    expect(sockets[0].closed).toBe(true)
    expect(stream.send('hello')).toBe(false)
  })

  it('reports the active session once its socket opens and ignores stale opens', () => {
    const sockets: FakeSocket[] = []
    const onOpen = vi.fn()
    const stream = new CodingStream({
      createSocket: () => {
        const socket = new FakeSocket()
        socket.readyState = 0
        sockets.push(socket)
        return socket
      },
      onEvent: vi.fn(),
      onOpen,
      onError: vi.fn(),
    })

    stream.connect('coding_1', 'ws://local/one')
    stream.connect('coding_2', 'ws://local/two')
    sockets[0].emitOpen()
    sockets[1].emitOpen()

    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(onOpen).toHaveBeenCalledWith('coding_2')
  })

  it('reports transport recovery from websocket truth', () => {
    vi.useFakeTimers()
    const sockets: FakeSocket[] = []
    const onConnectionState = vi.fn()
    const stream = new CodingStream({
      createSocket: () => {
        const socket = new FakeSocket()
        socket.readyState = 0
        sockets.push(socket)
        return socket
      },
      onEvent: vi.fn(),
      onConnectionState,
      onError: vi.fn(),
    })

    stream.connect('coding_1', 'ws://local/stream')
    sockets[0].emitOpen()
    sockets[0].onerror?.()
    sockets[0].readyState = 3
    sockets[0].onclose?.({ code: 1006, wasClean: false })
    vi.advanceTimersByTime(500)

    expect(onConnectionState.mock.calls).toEqual([
      ['coding_1', 'connecting'],
      ['coding_1', 'connected'],
      ['coding_1', 'recovering'],
      ['coding_1', 'recovering'],
    ])
    expect(sockets).toHaveLength(2)
    stream.disconnect()
    expect(onConnectionState).toHaveBeenLastCalledWith('coding_1', 'disconnected')
    vi.useRealTimers()
  })

  it('ignores events from an old socket after session switch', () => {
    const sockets: FakeSocket[] = []
    const onEvent = vi.fn()
    const stream = new CodingStream({
      createSocket: () => {
        const socket = new FakeSocket()
        sockets.push(socket)
        return socket
      },
      onEvent,
      onError: vi.fn(),
    })

    stream.connect('coding_1', 'ws://local/one')
    const oldSocket = sockets[0]
    stream.connect('coding_2', 'ws://local/two')
    oldSocket.emit(envelope(1, 'coding_1'))
    sockets[1].emit(envelope(1, 'coding_2'))

    expect(onEvent).toHaveBeenCalledTimes(1)
    expect(onEvent).toHaveBeenCalledWith('coding_2', envelope(1, 'coding_2'))
  })

  it('reconnects an abnormal close with the latest sequence cursor', () => {
    vi.useFakeTimers()
    const sockets: FakeSocket[] = []
    const urls: string[] = []
    const onEvent = vi.fn()
    const stream = new CodingStream({
      createSocket: (url) => {
        urls.push(url)
        const socket = new FakeSocket()
        sockets.push(socket)
        return socket
      },
      onEvent,
      onError: vi.fn(),
    })
    stream.connect('coding_1', 'ws://local/stream?after=0')
    sockets[0].emit(envelope(7))
    sockets[0].readyState = 3
    sockets[0].onclose?.()

    vi.advanceTimersByTime(500)

    expect(urls).toHaveLength(2)
    expect(new URL(urls[1]).searchParams.get('after')).toBe('7')
    sockets[1].emit(envelope(7))
    expect(onEvent).toHaveBeenCalledTimes(1)
    stream.disconnect()
    vi.useRealTimers()
  })

  it('does not reconnect a socket closed by a manual session switch', () => {
    vi.useFakeTimers()
    const sockets: FakeSocket[] = []
    const stream = new CodingStream({
      createSocket: () => {
        const socket = new FakeSocket()
        sockets.push(socket)
        return socket
      },
      onEvent: vi.fn(),
      onError: vi.fn(),
    })
    stream.connect('coding_1', 'ws://local/one?after=0')
    stream.connect('coding_2', 'ws://local/two?after=0')
    vi.advanceTimersByTime(10_000)

    expect(sockets).toHaveLength(2)
    stream.disconnect()
    vi.useRealTimers()
  })

  it('does not reconnect after clean close or policy violation', () => {
    vi.useFakeTimers()
    const sockets: FakeSocket[] = []
    const stream = new CodingStream({
      createSocket: () => { const socket = new FakeSocket(); sockets.push(socket); return socket },
      onEvent: vi.fn(),
      onError: vi.fn(),
    })
    stream.connect('coding_1', 'ws://local/stream?after=0')
    sockets[0].onclose?.({ code: 1008, wasClean: false })
    vi.advanceTimersByTime(10_000)
    expect(sockets).toHaveLength(1)
    stream.disconnect()
    vi.useRealTimers()
  })

  it('deduplicates replayed event ids after reconnect', () => {
    const socket = new FakeSocket()
    const onEvent = vi.fn()
    const stream = new CodingStream({ createSocket: () => socket, onEvent, onError: vi.fn() })
    stream.connect('coding_1', 'ws://local/stream?after=0')

    socket.emit(envelope(1))
    socket.emit(envelope(1))

    expect(onEvent).toHaveBeenCalledTimes(1)
  })

  it('reports and ignores malformed JSON, forwards raw events, and rejects invalid timeline envelopes', () => {
    const socket = new FakeSocket()
    const onEvent = vi.fn()
    const onError = vi.fn()
    const onRawEvent = vi.fn()
    const stream = new CodingStream({ createSocket: () => socket, onEvent, onError, onRawEvent })
    stream.connect('coding_1', 'ws://local/stream?after=0')

    socket.emitRaw('{not-json')
    // Raw event (no timeline envelope) -- forwarded to onRawEvent, not onError
    socket.emitRaw(JSON.stringify({ type: 'final', content: 'legacy-flat-event' }))
    socket.emitRaw(JSON.stringify({ ...envelope(1), kind: 'unknown' }))
    socket.emitRaw(JSON.stringify({ ...envelope(2), status: 'mystery' }))
    socket.emitRaw(JSON.stringify({ ...envelope(3), timestamp: '' }))

    expect(onEvent).not.toHaveBeenCalled()
    // Malformed JSON triggers onError; raw event goes to onRawEvent; invalid envelopes are ignored
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenLastCalledWith('无法解析运行事件')
    expect(onRawEvent).toHaveBeenCalledTimes(1)
    expect(onRawEvent).toHaveBeenCalledWith({ type: 'final', content: 'legacy-flat-event' })
    stream.disconnect()
  })

  it('bounds the transport deduplication cache', () => {
    const socket = new FakeSocket()
    const onEvent = vi.fn()
    const stream = new CodingStream({ createSocket: () => socket, onEvent, onError: vi.fn() })
    stream.connect('coding_1', 'ws://local/stream?after=0')

    for (let sequence = 1; sequence <= 2_050; sequence += 1) {
      socket.emit({ ...envelope(sequence), timestamp: `2026-07-12T00:00:${sequence}Z` })
    }
    socket.emit({ ...envelope(1), timestamp: '2026-07-12T00:00:01Z' })

    expect(onEvent).toHaveBeenCalledTimes(2_050)
    stream.disconnect()
  })
})
