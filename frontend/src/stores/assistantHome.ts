import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchAssistantHome } from '../api/assistant'
import type { AssistantHomeSummary } from '../types/api'

export const useAssistantHomeStore = defineStore('assistantHome', () => {
  const summary = ref<AssistantHomeSummary | null>(null)
  const loading = ref(false)
  const error = ref('')
  let requestGeneration = 0
  let inFlight: Promise<void> | null = null

  async function load(force = false): Promise<void> {
    if (summary.value && !force) return
    if (inFlight && !force) return inFlight
    const generation = ++requestGeneration
    loading.value = true
    error.value = ''
    const request = fetchAssistantHome()
      .then((result) => {
        if (generation !== requestGeneration) return
        summary.value = result
      })
      .catch((cause: unknown) => {
        if (generation !== requestGeneration) return
        error.value = cause instanceof Error ? cause.message : '首页摘要加载失败'
      })
      .finally(() => {
        if (generation === requestGeneration) loading.value = false
        if (inFlight === request) inFlight = null
      })
    inFlight = request
    return request
  }

  function invalidate() {
    requestGeneration += 1
    summary.value = null
    loading.value = false
    error.value = ''
    inFlight = null
  }

  return { summary, loading, error, load, invalidate }
})
