import type { ChatStartResponse } from '../types/api'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || window.location.origin

export async function startChat(content: string, userId = 'anonymous'): Promise<ChatStartResponse> {
  const response = await fetch(new URL('/api/v1/chat', API_BASE_URL), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, user_id: userId }),
  })

  if (!response.ok) {
    throw new Error(`Chat request failed with status ${response.status}`)
  }

  return (await response.json()) as ChatStartResponse
}

export function buildChatStreamUrl(sessionId: string): string {
  const base = new URL(API_BASE_URL, window.location.origin)
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
  base.pathname = `/api/v1/chat/${sessionId}/stream`
  base.search = ''
  return base.toString()
}
