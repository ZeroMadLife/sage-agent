import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useCodingStore } from '../stores/coding'
import { useHarnessSession } from './useHarnessSession'

describe('useHarnessSession', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('reuses the canonical session store without creating a session during navigation', async () => {
    const store = useCodingStore()
    store.loadSessions = vi.fn(async () => {
      store.codingSessions = [{
        session_id: 'session-2', title: '最近会话', workspace_root: '/tmp/project',
        created_at: '2026-07-17T00:00:00Z', updated_at: '2026-07-18T00:00:00Z',
        runtime_mode: 'default', runtime_profile: 'deerflow_v2', message_count: 2,
      }]
    })
    store.selectSession = vi.fn(async (sessionId: string) => { store.sessionId = sessionId })
    store.startNewSession = vi.fn()

    const harness = useHarnessSession()

    expect(await harness.connectExistingSession()).toBe(true)
    expect(store.selectSession).toHaveBeenCalledWith('session-2')
    expect(store.startNewSession).not.toHaveBeenCalled()
    expect(harness.store.$id).toBe('coding')
  })

  it('keeps session creation behind an explicit command', async () => {
    const store = useCodingStore()
    store.loadSessions = vi.fn(async () => { store.codingSessions = [] })
    store.startNewSession = vi.fn(async () => 'session-new')
    const harness = useHarnessSession()

    expect(await harness.connectExistingSession()).toBe(false)
    expect(store.startNewSession).not.toHaveBeenCalled()
    expect(await harness.createSession()).toBe('session-new')
    expect(localStorage.getItem('sage.coding.recentSessionId')).toBe('session-new')
  })
})
