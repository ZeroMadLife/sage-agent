import { nextTick } from 'vue'
import { afterEach, expect, it } from 'vitest'
import { closeCommandPalette, openCommandPalette, useCommandPalette } from './useCommandPalette'

afterEach(() => closeCommandPalette(false))

it('shares open state and restores focus to the invoking control', async () => {
  const trigger = document.createElement('button')
  document.body.appendChild(trigger)
  trigger.focus()

  openCommandPalette(trigger)
  expect(useCommandPalette().commandPaletteOpen.value).toBe(true)

  closeCommandPalette()
  await nextTick()
  expect(useCommandPalette().commandPaletteOpen.value).toBe(false)
  expect(document.activeElement).toBe(trigger)
  trigger.remove()
})
