import { ref } from 'vue'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || window.location.origin

export interface CloudUser {
  user_id: string
  email: string
  display_name: string
}

export interface CloudAuthOptions {
  canary_invite_login: boolean
  github_login: boolean
}

const defaultOptions: CloudAuthOptions = {
  canary_invite_login: false,
  github_login: true,
}

export function useCloudAuth() {
  const user = ref<CloudUser | null>(null)
  const error = ref('')

  async function options(): Promise<CloudAuthOptions> {
    try {
      const response = await fetch(new URL('/api/v1/cloud/auth/options', API_BASE_URL), {
        credentials: 'include',
        headers: { Accept: 'application/json' },
      })
      if (!response.ok) return defaultOptions
      return (await response.json()) as CloudAuthOptions
    } catch {
      return defaultOptions
    }
  }

  async function check(): Promise<boolean> {
    try {
      const response = await fetch(new URL('/api/v1/cloud/me', API_BASE_URL), {
        credentials: 'include',
        headers: { Accept: 'application/json' },
      })
      if (!response.ok) {
        user.value = null
        return false
      }
      user.value = (await response.json()) as CloudUser
      return true
    } catch {
      user.value = null
      error.value = '无法连接 Sage 认证服务'
      return false
    }
  }

  async function startGitHubLogin(inviteCode: string, returnTo: string): Promise<string> {
    error.value = ''
    const response = await fetch(new URL('/api/v1/cloud/auth/github/start', API_BASE_URL), {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ invite_code: inviteCode, return_to: returnTo }),
    })
    if (!response.ok) {
      const payload = (await response.json().catch(() => ({}))) as { detail?: string }
      throw new Error(payload.detail || 'GitHub 登录暂时不可用')
    }
    const payload = (await response.json()) as { authorization_url: string }
    const authorizationUrl = new URL(payload.authorization_url)
    if (authorizationUrl.protocol !== 'https:' || authorizationUrl.hostname !== 'github.com') {
      throw new Error('GitHub 登录地址无效')
    }
    return authorizationUrl.toString()
  }

  async function loginWithInvite(inviteCode: string, deviceName: string): Promise<CloudUser> {
    error.value = ''
    const response = await fetch(new URL('/api/v1/cloud/auth/canary/login', API_BASE_URL), {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ invite_code: inviteCode, device_name: deviceName }),
    })
    if (!response.ok) {
      const payload = (await response.json().catch(() => ({}))) as { detail?: string }
      throw new Error(payload.detail || '邀请码登录暂时不可用')
    }
    user.value = (await response.json()) as CloudUser
    return user.value
  }

  return { user, error, check, options, loginWithInvite, startGitHubLogin }
}
