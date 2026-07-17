import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import SageThinkingCharacter from './SageThinkingCharacter.vue'

describe('SageThinkingCharacter', () => {
  const drawImage = vi.fn()
  const clearRect = vi.fn()
  let animationCallback: FrameRequestCallback | undefined

  beforeEach(() => {
    drawImage.mockReset()
    clearRect.mockReset()
    animationCallback = undefined
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      clearRect,
      drawImage,
      imageSmoothingEnabled: true,
      imageSmoothingQuality: 'high',
    } as unknown as CanvasRenderingContext2D)
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      animationCallback = callback
      return 1
    })
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined)
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    } as unknown as MediaQueryList) })
    vi.stubGlobal('Image', class {
      complete = true
      naturalWidth = 1254
      naturalHeight = 1254
      onload: null | (() => void) = null
      onerror: null | (() => void) = null
      set src(_value: string) {
        queueMicrotask(() => this.onload?.())
      }
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('draws state rows from the sprite and advances internal frames', async () => {
    const wrapper = mount(SageThinkingCharacter, {
      props: { state: 'thinking', phase: '正在思考' },
    })
    await nextTick()
    await nextTick()

    expect(wrapper.get('canvas').attributes('width')).toBe('72')
    expect(drawImage).toHaveBeenCalled()
    expect(drawImage.mock.calls.at(-1)?.slice(1, 5)).toEqual([0, 209, 209, 209])

    animationCallback?.(500)
    expect(drawImage.mock.calls.at(-1)?.slice(1, 5)).toEqual([209, 209, 209, 209])

    await wrapper.setProps({ state: 'tool' })
    expect(drawImage.mock.calls.at(-1)?.slice(1, 5)).toEqual([0, 418, 209, 209])
  })

  it('keeps the first state frame when reduced motion is requested', async () => {
    mount(SageThinkingCharacter, {
      props: { state: 'waiting', reducedMotion: true },
    })
    await nextTick()
    await nextTick()

    animationCallback?.(2000)

    expect(drawImage.mock.calls.at(-1)?.slice(1, 5)).toEqual([0, 627, 209, 209])
  })

  it('falls back to the supplied static character when the sprite fails', async () => {
    vi.stubGlobal('Image', class {
      complete = false
      naturalWidth = 0
      naturalHeight = 0
      onload: null | (() => void) = null
      onerror: null | (() => void) = null
      set src(_value: string) {
        queueMicrotask(() => this.onerror?.())
      }
    })
    const wrapper = mount(SageThinkingCharacter, {
      props: { state: 'failed' },
    })
    await nextTick()
    await nextTick()

    expect(wrapper.find('img[alt="Sage"]').exists()).toBe(true)
  })
})
