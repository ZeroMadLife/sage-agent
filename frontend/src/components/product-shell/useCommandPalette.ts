import { nextTick, ref } from 'vue'

const commandPaletteOpen = ref(false)
let restoreTarget: HTMLElement | null = null

export function openCommandPalette(target?: HTMLElement | null) {
  const activeElement = document.activeElement instanceof HTMLElement ? document.activeElement : null
  restoreTarget = target ?? activeElement
  commandPaletteOpen.value = true
}

export function closeCommandPalette(restoreFocus = true) {
  commandPaletteOpen.value = false
  const target = restoreTarget
  restoreTarget = null
  if (restoreFocus && target?.isConnected) void nextTick(() => target.focus())
}

export function useCommandPalette() {
  return {
    commandPaletteOpen,
    openCommandPalette,
    closeCommandPalette,
  }
}
