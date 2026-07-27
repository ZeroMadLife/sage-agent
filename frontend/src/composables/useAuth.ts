const PASSPHRASE_KEY = 'sage_passphrase'
const USER_ID_KEY = 'sage_user_id'
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || window.location.origin

export function useAuth() {
  const storedPassphrase = localStorage.getItem(PASSPHRASE_KEY)
  const storedUserId = localStorage.getItem(USER_ID_KEY)

  async function verify(passphrase: string): Promise<boolean> {
    const response = await fetch(new URL('/api/v1/auth', API_BASE_URL).toString(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passphrase }),
    })
    if (!response.ok) {
      return false
    }

    const data = (await response.json()) as { valid: boolean; user_id: string }
    if (!data.valid) {
      return false
    }

    localStorage.setItem(PASSPHRASE_KEY, passphrase)
    localStorage.setItem(USER_ID_KEY, data.user_id)
    return true
  }

  function getUserId(): string {
    return localStorage.getItem(USER_ID_KEY) || ''
  }

  function isAuthenticated(): boolean {
    return Boolean(getUserId())
  }

  function logout(): void {
    localStorage.removeItem(PASSPHRASE_KEY)
    localStorage.removeItem(USER_ID_KEY)
  }

  return { storedPassphrase, storedUserId, verify, getUserId, isAuthenticated, logout }
}
