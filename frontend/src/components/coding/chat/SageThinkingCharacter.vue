<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import spriteUrl from '../../../assets/sage-thinking-sprite.png'
import fallbackUrl from '../../../assets/sage-thinking-fallback.png'

export type SageCharacterState = 'idle' | 'thinking' | 'tool' | 'waiting' | 'done' | 'failed'

const props = withDefaults(defineProps<{
  state: SageCharacterState
  phase?: string
  reducedMotion?: boolean
}>(), {
  phase: '',
  reducedMotion: undefined,
})

const canvas = ref<HTMLCanvasElement | null>(null)
const failed = ref(false)
const stateRows: Record<SageCharacterState, number> = {
  idle: 0,
  thinking: 1,
  tool: 2,
  waiting: 3,
  done: 4,
  failed: 5,
}
const frameIntervals: Record<SageCharacterState, number> = {
  idle: 420,
  thinking: 320,
  tool: 250,
  waiting: 460,
  done: 230,
  failed: 360,
}

let sprite: HTMLImageElement | null = null
let animationFrame = 0
let frameIndex = 0
let lastFrameAt = 0
let mediaQuery: MediaQueryList | null = null
let mediaReducedMotion = false

function shouldReduceMotion() {
  return props.reducedMotion ?? mediaReducedMotion
}

function drawFrame() {
  const element = canvas.value
  if (!element || !sprite?.complete || !sprite.naturalWidth) return
  const context = element.getContext('2d')
  if (!context) return
  const frameWidth = sprite.naturalWidth / 6
  const frameHeight = sprite.naturalHeight / 6
  const row = stateRows[props.state]
  context.clearRect(0, 0, element.width, element.height)
  context.imageSmoothingEnabled = true
  context.imageSmoothingQuality = 'high'
  context.drawImage(
    sprite,
    frameIndex * frameWidth,
    row * frameHeight,
    frameWidth,
    frameHeight,
    0,
    0,
    element.width,
    element.height,
  )
}

function animate(timestamp: number) {
  if (!shouldReduceMotion() && timestamp - lastFrameAt >= frameIntervals[props.state]) {
    frameIndex = (frameIndex + 1) % 6
    lastFrameAt = timestamp
    drawFrame()
  }
  animationFrame = window.requestAnimationFrame(animate)
}

function resetState() {
  frameIndex = 0
  lastFrameAt = 0
  drawFrame()
}

function handleMotionChange(event: MediaQueryListEvent) {
  mediaReducedMotion = event.matches
  if (mediaReducedMotion) resetState()
}

onMounted(() => {
  mediaQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)') ?? null
  mediaReducedMotion = mediaQuery?.matches ?? false
  mediaQuery?.addEventListener?.('change', handleMotionChange)
  sprite = new Image()
  sprite.onload = () => {
    failed.value = false
    resetState()
  }
  sprite.onerror = () => {
    failed.value = true
  }
  sprite.src = spriteUrl
  animationFrame = window.requestAnimationFrame(animate)
})

watch(() => props.state, resetState)
watch(() => props.reducedMotion, () => {
  if (shouldReduceMotion()) resetState()
})

onBeforeUnmount(() => {
  window.cancelAnimationFrame(animationFrame)
  mediaQuery?.removeEventListener?.('change', handleMotionChange)
  if (sprite) {
    sprite.onload = null
    sprite.onerror = null
  }
})
</script>

<template>
  <span class="sage-character" :data-state="state">
    <canvas
      v-show="!failed"
      ref="canvas"
      width="72"
      height="72"
      role="img"
      :aria-label="phase ? `Sage ${phase}` : 'Sage 运行状态'"
    ></canvas>
    <img v-if="failed" :src="fallbackUrl" alt="Sage" />
  </span>
</template>

<style scoped>
.sage-character { display:block; flex:none; width:62px; height:62px; overflow:hidden; }
.sage-character canvas,.sage-character img { display:block; width:100%; height:100%; object-fit:contain; }
.sage-character img { border-radius:var(--sage-radius); }
@media (max-width:640px) { .sage-character { width:54px; height:54px; } }
</style>
