import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatStore } from './chat'

const startChatMock = vi.fn()

vi.mock('../api/chat', () => ({
  startChat: (...args: unknown[]) => startChatMock(...args),
  buildChatStreamUrl: (id: string) => `ws://localhost/api/v1/chat/${id}/stream`,
}))

type SocketHandler = ((event?: Event | MessageEvent) => void) | null

class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static instances: FakeWebSocket[] = []

  readonly url: string
  onopen: SocketHandler = null
  onmessage: SocketHandler = null
  onerror: SocketHandler = null
  onclose: SocketHandler = null
  readyState = FakeWebSocket.CONNECTING
  sentMessages: string[] = []

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.(new Event('close'))
  }

  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.(new Event('open'))
  }

  receive(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }

  send(data: string) {
    if (this.readyState !== FakeWebSocket.OPEN) {
      throw new Error('socket is not open')
    }
    this.sentMessages.push(data)
  }
}

describe('chat store websocket flow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    FakeWebSocket.instances = []
    startChatMock.mockReset()
    startChatMock.mockResolvedValue({ session_id: 'session-001' })
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('waits for the first websocket connection to open before sending the message', async () => {
    vi.useFakeTimers()
    const store = useChatStore()

    const sendPromise = store.sendMessage('你好', 'device-001')
    await Promise.resolve()
    await Promise.resolve()

    const socket = FakeWebSocket.instances[0]
    expect(socket.sentMessages).toEqual([])

    await vi.advanceTimersByTimeAsync(250)
    expect(socket.sentMessages).toEqual([])

    socket.open()
    await sendPromise
    expect(socket.sentMessages).toEqual([JSON.stringify({ content: '你好' })])
  })

  it('records progress, tool call, and result events from the websocket', async () => {
    const store = useChatStore()

    const sendPromise = store.sendMessage('杭州天气', 'device-001')
    await Promise.resolve()
    await Promise.resolve()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    await sendPromise

    socket.receive({ type: 'progress', agent: 'agent', message: '正在思考...' })
    socket.receive({ type: 'tool_call', tool: 'get_weather', args: { city: '杭州' } })
    socket.receive({
      type: 'result',
      content: '杭州现在28度，晴天。',
      itinerary: null,
      tool_calls: [{ tool: 'get_weather', error: '' }],
      metrics: { latency_ms: 500 },
    })

    expect(store.events[0].message).toBe('正在思考...')
    expect(store.toolCalls[0].tool).toBe('get_weather')
    expect(store.result?.content).toContain('28度')
    expect(store.isExecuting).toBe(false)
  })
})
