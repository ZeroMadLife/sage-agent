<script setup lang="ts">
import { computed } from 'vue'
import { Minimize2 } from 'lucide-vue-next'
import { useCodingStore } from '../../../stores/coding'

const store = useCodingStore()

const used = computed(() => store.contextSnapshot?.used_tokens ?? store.contextChars)
const total = computed(() => store.contextSnapshot?.model_limit_tokens ?? 0)
const remaining = computed(() => Math.max(total.value - used.value, 0))
const percent = computed(() => total.value > 0 ? Math.min(100, (used.value / total.value) * 100) : 0)
const label = computed(() => `${compactNumber(used.value)} / ${compactNumber(total.value)} · 剩余 ${compactNumber(remaining.value)}`)
const tooltip = computed(() => {
  const snapshot = store.contextSnapshot
  if (!snapshot) return ''
  const effective = snapshot.effective_limit_tokens ?? 0
  const reserve = snapshot.output_reserve_tokens ?? 0
  const estimate = snapshot.estimated ? ' · 估算值' : ''
  return `上下文 ${used.value.toLocaleString()} / ${total.value.toLocaleString()} tokens · 可用 ${effective.toLocaleString()} · 输出预留 ${reserve.toLocaleString()}${estimate}`
})

function compactNumber(value: number) {
  if (value >= 1_000_000) return `${formatDecimal(value / 1_000_000)}M`
  if (value >= 1_000) return `${formatDecimal(value / 1_000)}k`
  return value.toLocaleString()
}

function formatDecimal(value: number) {
  return value.toFixed(1)
}
</script>

<template>
  <div
    v-if="store.contextConfigured"
    class="context-summary"
    :title="tooltip"
    role="progressbar"
    :aria-label="tooltip"
    :aria-valuenow="Math.round(percent)"
    aria-valuemin="0"
    aria-valuemax="100"
  >
    <span class="context-copy context-copy-desktop">{{ label }}</span>
    <span class="context-copy context-copy-mobile">{{ compactNumber(used) }} / {{ compactNumber(total) }}</span>
    <span class="context-track" aria-hidden="true"><span :style="{ width: `${percent}%` }"></span></span>
    <button
      v-if="store.contextCompactable"
      class="compact-context"
      type="button"
      title="压缩上下文"
      aria-label="压缩上下文"
      :disabled="store.isThinking || store.contextBusy"
      @click="store.compactContext()"
    >
      <Minimize2 :size="13" />
    </button>
  </div>
</template>

<style scoped>
.context-summary {
  display: grid;
  grid-template-columns: minmax(0, auto) 48px auto;
  align-items: center;
  gap: 7px;
  min-width: 0;
  color: var(--sage-text-muted);
  font-size: 10.5px;
  white-space: nowrap;
}

.context-copy { overflow: hidden; text-overflow: ellipsis; }
.context-copy-mobile { display: none; }
.context-track { width: 48px; height: 4px; overflow: hidden; border-radius: 999px; background: var(--sage-surface-muted); }
.context-track span { display: block; height: 100%; border-radius: inherit; background: var(--sage-text-muted); transition: width .2s ease; }
.compact-context { display: grid; place-items: center; width: 26px; height: 26px; padding: 0; border: 1px solid transparent; border-radius: var(--sage-radius); color: var(--sage-text-muted); background: transparent; }
.compact-context:hover { border-color: var(--sage-border); color: var(--sage-text); background: var(--sage-surface-muted); }

@media (max-width: 720px) {
  .context-summary { flex: none; grid-template-columns: 74px 28px; width: 109px; }
  .context-copy-desktop { display: none; }
  .context-copy-mobile { display: block; }
  .context-track { width: 28px; }
  .compact-context { display: none; }
  .context-copy { font-size: var(--sage-font-xs); }
}
</style>
