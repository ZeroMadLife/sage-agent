import { useCodingStore } from '../stores/coding'
import type { HarnessSurfaceContext } from './types'

const RECENT_SESSION_KEY = 'sage.coding.recentSessionId'

export function useHarnessSession() {
  const store = useCodingStore()

  async function connectExistingSession(): Promise<boolean> {
    if (store.sessionId) {
      await store.restoreCurrentSession()
      return true
    }

    await store.loadSessions()
    const recentSessionId = localStorage.getItem(RECENT_SESSION_KEY) || ''
    const candidates = store.codingSessions
      .filter((session) => !session.archived)
      .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))
    const target = candidates.find((session) => session.session_id === recentSessionId)
      ?? candidates[0]
    if (!target) return false

    await store.selectSession(target.session_id)
    localStorage.setItem(RECENT_SESSION_KEY, target.session_id)
    return true
  }

  async function createSession(): Promise<string> {
    const sessionId = await store.startNewSession()
    if (sessionId) localStorage.setItem(RECENT_SESSION_KEY, sessionId)
    return sessionId
  }

  function send(content: string, context?: HarnessSurfaceContext | null) {
    return store.sendMessage(content, context)
  }

  function detach() {
    store.disconnect()
  }

  return {
    store,
    connectExistingSession,
    createSession,
    send,
    detach,
  }
}
