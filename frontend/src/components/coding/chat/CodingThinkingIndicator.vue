<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import SageThinkingAvatar from './SageThinkingAvatar.vue'

defineProps<{
  phase: string
}>()

const elapsedSeconds = ref(0)
const elapsedLabel = computed(() => `${elapsedSeconds.value}s`)
let timer: ReturnType<typeof setInterval> | undefined

onMounted(() => {
  timer = setInterval(() => { elapsedSeconds.value += 1 }, 1_000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="thinking-indicator" role="status" aria-live="polite">
    <span class="thinking-sheen" aria-hidden="true"></span>
    <SageThinkingAvatar />
    <span class="thinking-copy">
      <span class="thinking-title"><strong>正在思考</strong><span class="thinking-time">{{ elapsedLabel }}</span></span>
      <span class="thinking-phase">{{ phase }}</span>
    </span>
  </div>
</template>

<style scoped>
.thinking-indicator {
  display: flex;
  position: relative;
  overflow: hidden;
  width: fit-content;
  align-items: center;
  gap: 10px;
  max-width: 100%;
  margin: 0 0 12px;
  padding: 7px 12px 7px 8px;
  border: 0;
  border-radius: var(--sage-radius);
  background: linear-gradient(100deg, var(--sage-surface) 25%, color-mix(in srgb, var(--sage-warning-bg) 68%, var(--sage-surface)) 50%, var(--sage-surface) 75%);
  background-size: 220% 100%;
  font-size: 13px;
  color: var(--sage-text-secondary);
}

.thinking-indicator { animation: thinking-sheen 2.2s ease-in-out infinite; }
.thinking-sheen { position:absolute; inset:0; pointer-events:none; background:linear-gradient(90deg, transparent, rgb(255 255 255 / 14%), transparent); transform:translateX(-100%); animation:thinking-sweep 1.9s ease-in-out infinite; }
.thinking-copy { display:grid; gap:2px; min-width:0; }
.thinking-title { display:flex; align-items:baseline; gap:7px; min-width:0; }
.thinking-copy strong { color:var(--sage-text); font-size:12px; }
.thinking-time { color:var(--sage-text-muted); font-family:var(--sage-font-mono); font-size:10px; }

@keyframes thinking-sheen { 0%,100% { background-position: 100% 0; } 50% { background-position: 0 0; } }
@keyframes thinking-sweep { 0% { transform:translateX(-100%); } 55%,100% { transform:translateX(100%); } }

.thinking-phase {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color:var(--sage-text-muted);
  font-size:11px;
}

@media (prefers-reduced-motion: reduce) {
  .thinking-indicator, .thinking-sheen { animation: none; }
}

</style>
