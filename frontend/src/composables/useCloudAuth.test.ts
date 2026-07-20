import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useCloudAuth } from './useCloudAuth'

describe('useCloudAuth', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('treats a missing server session as unauthenticated', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('', { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)
    const auth = useCloudAuth()

    expect(await auth.check()).toBe(false)
    expect(auth.user.value).toBeNull()
    expect(fetchMock).toHaveBeenCalledWith(
      expect.objectContaining({ pathname: '/api/v1/cloud/me' }),
      expect.objectContaining({ credentials: 'include' }),
    )
  })

  it('resolves the current cloud user from an HttpOnly session', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            user_id: 'user-1',
            email: 'owner@example.com',
            display_name: 'Owner',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )
    const auth = useCloudAuth()

    expect(await auth.check()).toBe(true)
    expect(auth.user.value?.user_id).toBe('user-1')
  })

  it('posts the invite without placing it in the authorization URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ authorization_url: 'https://github.com/login/oauth/authorize?state=opaque' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)
    const auth = useCloudAuth()

    const authorizationUrl = await auth.startGitHubLogin('one-time-secret', '/#/coding')

    expect(authorizationUrl).not.toContain('one-time-secret')
    expect(fetchMock).toHaveBeenCalledWith(
      expect.objectContaining({ pathname: '/api/v1/cloud/auth/github/start' }),
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        body: JSON.stringify({ invite_code: 'one-time-secret', return_to: '/#/coding' }),
      }),
    )
  })

  it('exchanges an invite for a same-origin device session', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          user_id: 'user-1',
          email: 'owner@example.com',
          display_name: 'owner',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { loginWithInvite, user } = useCloudAuth()
    const loggedIn = await loginWithInvite('one-time-code', 'iPhone Safari')

    expect(loggedIn.email).toBe('owner@example.com')
    expect(user.value).toEqual(loggedIn)
    expect(fetchMock).toHaveBeenCalledWith(
      expect.objectContaining({ pathname: '/api/v1/cloud/auth/canary/login' }),
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        body: JSON.stringify({ invite_code: 'one-time-code', device_name: 'iPhone Safari' }),
      }),
    )
  })

  it('reads the enabled login methods from the server', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ canary_invite_login: true, github_login: true }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    const { options } = useCloudAuth()

    await expect(options()).resolves.toEqual({
      canary_invite_login: true,
      github_login: true,
    })
  })

  it('rejects a non-GitHub authorization URL', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ authorization_url: 'https://example.com/steal' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    const auth = useCloudAuth()

    await expect(auth.startGitHubLogin('invite', '/#/coding')).rejects.toThrow(
      'GitHub 登录地址无效',
    )
  })
})
