<script setup lang="ts">
import { computed } from 'vue'
import ToolCallStatusComponent from './ToolCallStatus.vue'

type ToolStatus = 'running' | 'done' | 'error'

const props = defineProps<{
  isThinking?: boolean
  toolCalls: Array<{
    tool: string
    args: Record<string, unknown>
    status: ToolStatus
    message?: string
  }>
}>()

const hasRunning = computed(() => props.toolCalls.some((call) => call.status === 'running'))
const hasError = computed(() => props.toolCalls.some((call) => call.status === 'error'))
const latestRunning = computed(() => [...props.toolCalls].reverse().find((call) => call.status === 'running'))
const errorCount = computed(() => props.toolCalls.filter((call) => call.status === 'error').length)

const summaryTitle = computed(() => {
  if (props.isThinking || hasRunning.value) {
    return '思考中'
  }
  if (hasError.value) {
    return '部分工具失败'
  }
  return '思考过程'
})

const summaryMeta = computed(() => {
  if (latestRunning.value?.message) {
    return latestRunning.value.message
  }
  if (hasRunning.value) {
    return '正在调用工具'
  }
  if (props.isThinking) {
    return '正在整理工具结果'
  }
  const count = props.toolCalls.length
  if (hasError.value) {
    return `已调用 ${count} 个工具，${errorCount.value} 个失败`
  }
  return count > 0 ? `已调用 ${count} 个工具` : '未调用工具'
})
</script>

<template>
  <details v-if="toolCalls.length > 0" class="tool-trace" :open="hasRunning">
    <summary>
      <span class="chevron">›</span>
      <span class="title">{{ summaryTitle }}</span>
      <span class="meta">{{ summaryMeta }}</span>
    </summary>
    <div class="tool-list">
      <ToolCallStatusComponent
        v-for="(call, i) in toolCalls"
        :key="`${call.tool}-${i}`"
        :tool="call.tool"
        :args="call.args"
        :status="call.status"
        :message="call.message"
      />
    </div>
  </details>
</template>

<style scoped>
.tool-trace {
  margin: 0.35rem 0 0.6rem;
  border-radius: 8px;
  background: #f3f4f6;
  color: #374151;
  font-size: 0.88rem;
}

summary {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  min-height: 2.1rem;
  padding: 0.35rem 0.55rem;
  cursor: pointer;
  list-style: none;
}

summary::-webkit-details-marker {
  display: none;
}

.chevron {
  color: #9ca3af;
  font-size: 1rem;
  transition: transform 0.15s ease;
}

.tool-trace[open] .chevron {
  transform: rotate(90deg);
}

.title {
  font-weight: 600;
  color: #111827;
  white-space: nowrap;
}

.meta {
  min-width: 0;
  color: #6b7280;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-list {
  padding: 0.2rem 0.55rem 0.55rem;
}
</style>
