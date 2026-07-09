<script setup lang="ts">
import { computed, ref } from 'vue'
import { Send, Square } from 'lucide-vue-next'
import { useCodingStore } from '../../../stores/coding'

const store = useCodingStore()
const input = ref('')

const canSend = computed(
  () => Boolean(input.value.trim()) && Boolean(store.sessionId) && !store.isThinking,
)

const contextColor = computed(() => {
  const pct = store.contextPercent
  if (pct > 80) return '#ef4444'
  if (pct > 60) return '#f59e0b'
  return '#10b981'
})

const circumference = 2 * Math.PI * 14
const dashOffset = computed(
  () => circumference - (store.contextPercent / 100) * circumference,
)
const contextTooltip = computed(
  () =>
    `${store.contextChars} chars used / ${store.contextBudget} budget (${store.contextPercent}%)`,
)

function send() {
  const content = input.value.trim()
  if (!content) return
  store.sendMessage(content)
  input.value = ''
}

function stop() {
  void store.stopCurrentRun()
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    send()
  }
}

defineExpose({
  setInput(value: string) {
    input.value = value
  },
})
</script>

<template>
  <div class="composer">
    <div class="composer-controls">
      <select
        v-model="store.currentModelId"
        class="model-select"
        @change="store.changeModel(store.currentModelId)"
      >
        <option v-for="model in store.models" :key="model.id" :value="model.id">
          {{ model.label }}
        </option>
      </select>

      <button
        v-if="store.contextPercent > 75"
        class="compact-hint"
        type="button"
        :title="contextTooltip"
      >
        压缩
      </button>

      <div class="context-ring" :title="contextTooltip">
        <svg width="32" height="32" viewBox="0 0 32 32">
          <circle cx="16" cy="16" r="14" fill="none" stroke="#e5e7eb" stroke-width="3" />
          <circle
            cx="16"
            cy="16"
            r="14"
            fill="none"
            :stroke="contextColor"
            stroke-width="3"
            :stroke-dasharray="circumference"
            :stroke-dashoffset="dashOffset"
            transform="rotate(-90 16 16)"
            stroke-linecap="round"
          />
        </svg>
        <span class="context-pct">{{ store.contextPercent }}%</span>
      </div>
    </div>

    <div class="composer-input">
      <textarea
        v-model="input"
        rows="2"
        :disabled="!store.sessionId || store.isThinking"
        placeholder="输入任务，或 /review 调用 skill"
        @keydown="onKeydown"
      />
      <button
        v-if="store.isThinking"
        class="stop-btn"
        type="button"
        title="Stop current run"
        @click="stop"
      >
        <Square :size="13" />
      </button>
      <button v-else :disabled="!canSend" class="send-btn" @click="send">
        <Send :size="15" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.composer {
  border-top: 1px solid #e5e7eb;
  background: #fff;
  padding: 10px 16px;
}

.composer-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.model-select {
  border: 1px solid #d1d5db;
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 12px;
  background: #fff;
  color: #374151;
  cursor: pointer;
}

.context-ring {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-left: auto;
}

.compact-hint {
  margin-left: auto;
  border: 1px solid #f59e0b;
  border-radius: 6px;
  background: #fffbeb;
  color: #92400e;
  padding: 3px 7px;
  font-size: 11px;
  font-weight: 700;
}

.compact-hint + .context-ring {
  margin-left: 0;
}

.context-pct {
  position: absolute;
  font-size: 9px;
  font-weight: 700;
  color: #4b5563;
}

.composer-input {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

.composer-input textarea {
  flex: 1;
  resize: vertical;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  padding: 8px 10px;
  line-height: 1.5;
  font-size: 13px;
  font-family: inherit;
}

.composer-input textarea:focus {
  outline: none;
  border-color: #3b82f6;
}

.send-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 0;
  border-radius: 8px;
  background: #111827;
  color: #fff;
  cursor: pointer;
}

.stop-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid #ef4444;
  border-radius: 8px;
  background: #fff;
  color: #dc2626;
  cursor: pointer;
}

.send-btn:disabled {
  background: #d1d5db;
  cursor: not-allowed;
}
</style>
