import { afterEach, expect, it, vi } from 'vitest'
import { runViewTransition } from './useViewTransition'

type TransitionDocument = Document & {
  startViewTransition?: (update: () => Promise<void>) => {
    updateCallbackDone: Promise<void>
    finished: Promise<void>
  }
}

function setReducedMotion(matches: boolean) {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  Reflect.deleteProperty(document as TransitionDocument, 'startViewTransition')
  delete document.documentElement.dataset.viewTransition
})

it('runs the update directly when the browser has no View Transition API', async () => {
  setReducedMotion(false)
  const update = vi.fn().mockResolvedValue(undefined)

  await runViewTransition(update)

  expect(update).toHaveBeenCalledTimes(1)
  expect(document.documentElement.dataset.viewTransition).toBeUndefined()
})

it('uses a named View Transition and waits for the route update', async () => {
  setReducedMotion(false)
  const update = vi.fn().mockResolvedValue(undefined)
  const start = vi.fn((callback: () => Promise<void>) => {
    const updateCallbackDone = callback()
    return { updateCallbackDone, finished: updateCallbackDone }
  })
  Object.defineProperty(document, 'startViewTransition', { configurable: true, value: start })

  await runViewTransition(update, 'composer')

  expect(start).toHaveBeenCalledTimes(1)
  expect(update).toHaveBeenCalledTimes(1)
  expect(document.documentElement.dataset.viewTransition).toBeUndefined()
})

it('skips animation when the user requests reduced motion', async () => {
  setReducedMotion(true)
  const update = vi.fn().mockResolvedValue(undefined)
  const start = vi.fn()
  Object.defineProperty(document, 'startViewTransition', { configurable: true, value: start })

  await runViewTransition(update)

  expect(start).not.toHaveBeenCalled()
  expect(update).toHaveBeenCalledTimes(1)
})
