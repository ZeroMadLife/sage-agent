import { ref, watch, type Ref } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'

const KEYS = {
  showToolProcess: 'sage.workbench.showToolProcess',
  themeMode: 'sage.ui.theme',
} as const

let systemThemeListenerInstalled = false
let requestedThemeMode: ThemeMode = 'light'

function resolvedTheme(mode: ThemeMode): 'light' | 'dark' {
  if (mode !== 'system') return mode
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/** Applies the resolved preference at document scope so routed views share one visual system. */
export function applyThemeMode(mode: ThemeMode) {
  requestedThemeMode = mode
  document.documentElement.dataset.theme = resolvedTheme(mode)

  if (!systemThemeListenerInstalled && window.matchMedia) {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    media.addEventListener?.('change', () => {
      if (requestedThemeMode === 'system') applyThemeMode('system')
    })
    systemThemeListenerInstalled = true
  }
}

function booleanPreference(key: string, fallback: boolean): Ref<boolean> {
  const stored = localStorage.getItem(key)
  const value = ref(stored === null ? fallback : stored === 'true')
  watch(value, (next) => localStorage.setItem(key, String(next)), { flush: 'sync' })
  return value
}

function themePreference() {
  const stored = localStorage.getItem(KEYS.themeMode)
  const value = ref<ThemeMode>(stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'light')
  applyThemeMode(value.value)
  watch(value, (next) => {
    localStorage.setItem(KEYS.themeMode, next)
    applyThemeMode(next)
  }, { flush: 'sync' })
  return value
}

export function useWorkbenchPreferences() {
  return {
    showToolProcess: booleanPreference(KEYS.showToolProcess, true),
    themeMode: themePreference(),
  }
}
