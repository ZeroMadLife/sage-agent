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
      toolCalls.value.push(event)
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
  }

  async function sendMessage(content: string, userId = 'anonymous') {
    _resetTurnState()
    isExecuting.value = true

    if (!currentSessionId.value || !activeSocket || activeSocket.readyState !== WebSocket.OPEN) {
      // 创建新 session
      const response = await startChat(content, userId)
      currentSessionId.value = response.session_id
      _connect(response.session_id)
      // 等连接建立后发消息
      await new Promise((resolve) => setTimeout(resolve, 200))
      if (activeSocket?.readyState === WebSocket.OPEN) {
        activeSocket.send(JSON.stringify({ content }))
      }
    } else {
      // 已有 session, 通过 WebSocket 发消息
      activeSocket.send(JSON.stringify({ content }))
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
