import type { AssistantHomeSummary } from '../types/api'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || window.location.origin

export async function fetchAssistantHome(): Promise<AssistantHomeSummary> {
  const response = await fetch(new URL('/api/v1/assistant/home', API_BASE_URL), {
    credentials: 'include',
  })
  if (!response.ok) {
    if (response.status === 401) throw new Error('登录状态已失效，请重新登录')
    throw new Error(`首页摘要加载失败：${response.status}`)
  }
  return (await response.json()) as AssistantHomeSummary
}
