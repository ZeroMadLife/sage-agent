import { beforeEach, describe, expect, it } from 'vitest'
import { applyThemeMode, useWorkbenchPreferences } from './useWorkbenchPreferences'

describe('useWorkbenchPreferences', () => {
  beforeEach(() => localStorage.clear())

it('persists process visibility without retaining a removed inspector layout', () => {
  const preferences = useWorkbenchPreferences()
  preferences.showToolProcess.value = false

  const restored = useWorkbenchPreferences()
  expect(restored.showToolProcess.value).toBe(false)
})

it('persists the requested theme mode', () => {
  const preferences = useWorkbenchPreferences()
  preferences.themeMode.value = 'dark'

  expect(localStorage.getItem('sage.ui.theme')).toBe('dark')
})

it('applies the resolved theme to the application root', () => {
  applyThemeMode('dark')
  expect(document.documentElement.dataset.theme).toBe('dark')

  applyThemeMode('light')
  expect(document.documentElement.dataset.theme).toBe('light')
})
})
