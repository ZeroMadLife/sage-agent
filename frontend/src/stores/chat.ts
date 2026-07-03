import { defineStore } from 'pinia'
import { ref } from 'vue'
import { buildChatStreamUrl, startChat } from '../api/chat'
import type {
  AgentResultEvent,
  ErrorEvent,
  ProgressEvent,
  ServerEvent,
  ToolCallEvent,
} from '../types/api'

export const useChatStore = defineStore('chat', () => {
  const events = ref<ProgressEvent[]>([])
  const toolCalls = ref<ToolCallEvent[]>([])
  const errors = ref<ErrorEvent[]>([])
  const result = ref<AgentResultEvent | null>(null)
  const currentSessionId = ref('')
  const isExecuting = ref(false)
  let activeSocket: WebSocket | null = null

  function _resetTurnState() {
    events.value = []
    toolCalls.value = []
    errors.value = []
    result.value = null
  }

  function _handleServerEvent(event: ServerEvent) {
    if (event.type === 'progress') {
      events.value.push(event)
    } else if (event.type === 'tool_call') {
      const normalized = {
        ...event,
        status: event.status ?? 'done',
        message: event.message ?? '',
      }
      const key = JSON.stringify({ tool: normalized.tool, args: normalized.args })
      let existingIndex = -1
      for (let index = toolCalls.value.length - 1; index >= 0; index -= 1) {
        const current = toolCalls.value[index]
        if (JSON.stringify({ tool: current.tool, args: current.args }) === key) {
          existingIndex = index
          break
        }
      }
      if (existingIndex >= 0 && normalized.status !== 'running') {
        toolCalls.value[existingIndex] = normalized
      } else {
        toolCalls.value.push(normalized)
      }
    } else if (event.type === 'result') {
      result.value = event
      isExecuting.value = false
    } else if (event.type === 'error') {
      errors.value.push(event)
      isExecuting.value = false
    } else if (event.type === 'busy') {
      errors.value.push({ type: 'error', message: event.message, recoverable: true })
    }
  }

  function _connect(sessionId: string) {
    if (activeSocket) {
      activeSocket.close()
    }
    const socket = new WebSocket(buildChatStreamUrl(sessionId))
    activeSocket = socket

    socket.onmessage = (msg: MessageEvent) => {
      const event = JSON.parse(String(msg.data)) as ServerEvent
      _handleServerEvent(event)
    }
    socket.onerror = () => {
      errors.value.push({
        type: 'error',
        message: 'WebSocket连接失败',
        recoverable: true,
      })
      isExecuting.value = false
    }
    socket.onclose = () => {
      if (isExecuting.value) {
        isExecuting.value = false
      }
    }
    return socket
  }

  function _waitForOpen(socket: WebSocket): Promise<void> {
    if (socket.readyState === WebSocket.OPEN) {
      return Promise.resolve()
    }
    if (socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING) {
      return Promise.reject(new Error('WebSocket连接已关闭'))
    }

    return new Promise((resolve, reject) => {
      const originalOpen = socket.onopen
      const originalError = socket.onerror

      socket.onopen = (event: Event) => {
        originalOpen?.call(socket, event)
        resolve()
      }
      socket.onerror = (event: Event) => {
        originalError?.call(socket, event)
        reject(new Error('WebSocket连接失败'))
      }
    })
  }

  function _sendSocketMessage(socket: WebSocket, content: string) {
    socket.send(JSON.stringify({ content }))
  }

  async function sendMessage(content: string, userId = 'anonymous') {
    _resetTurnState()
    isExecuting.value = true

    try {
      let socket = activeSocket
      if (
        !currentSessionId.value ||
        !socket ||
        socket.readyState === WebSocket.CLOSED ||
        socket.readyState === WebSocket.CLOSING
      ) {
        const response = await startChat(content, userId)
        currentSessionId.value = response.session_id
        socket = _connect(response.session_id)
      }

      await _waitForOpen(socket)
      _sendSocketMessage(socket, content)
    } catch (error) {
      errors.value.push({
        type: 'error',
        message: error instanceof Error ? error.message : '消息发送失败',
        recoverable: true,
      })
      isExecuting.value = false
    }
  }

  function closeStream() {
    activeSocket?.close()
    activeSocket = null
  }

  return {
    events,
    toolCalls,
    errors,
    result,
    currentSessionId,
    isExecuting,
    sendMessage,
    closeStream,
  }
})
