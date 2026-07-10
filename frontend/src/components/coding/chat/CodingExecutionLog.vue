<script setup lang="ts">
import { CheckCircle2, ChevronDown, ChevronRight, Circle, History, RotateCcw, Wrench, XCircle } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type { ExecutionActivity } from '../../../stores/codingEvents'

const props = defineProps<{
  activities: ExecutionActivity[]
  isThinking: boolean
}>()

const expanded = ref(false)
const runningCount = computed(() => props.activities.filter((item) => item.status === 'running').length)
const errorCount = computed(() => props.activities.filter((item) => item.status === 'error').length)
const isOpen = computed(() => expanded.value || props.isThinking)

function iconFor(activity: ExecutionActivity) {
  if (activity.status === 'error') return XCircle
  if (activity.status === 'running') return Circle
  if (activity.kind === 'tool') return Wrench
  if (activity.kind === 'retry') return RotateCcw
  return CheckCircle2
}

function toggle() {
  expanded.value = !expanded.value
}
</script>

<template>
  <section v-if="activities.length" class="execution-log" :class="{ open: isOpen }">
    <button class="execution-log-header" type="button" @click="toggle">
      <component :is="isOpen ? ChevronDown : ChevronRight" :size="14" />
      <History :size="13" />
      <span class="execution-log-title">执行过程</span>
      <span v-if="runningCount" class="execution-status running">进行中</span>
      <span v-else-if="errorCount" class="execution-status error">有错误</span>
      <span v-else class="execution-status done">已完成</span>
    </button>
    <div v-if="isOpen" class="execution-log-list">
      <div v-for="(activity, index) in activities" :key="index" class="execution-log-item">
        <component :is="iconFor(activity)" :size="13" :class="activity.status" />
        <span class="execution-log-label">{{ activity.label }}</span>
        <span v-if="activity.detail" class="execution-log-detail">{{ activity.detail }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.execution-log {
  max-width: 760px;
  margin: 0 0 5px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fafafa;
  overflow: hidden;
}

.execution-log-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 10px;
  border: 0;
  background: transparent;
  color: #4b5563;
  cursor: pointer;
  font-size: 12px;
  text-align: left;
}

.execution-log-header:hover {
  background: #f3f4f6;
}

.execution-log-title {
  font-weight: 600;
}

.execution-status {
  margin-left: auto;
  font-size: 11px;
}

.execution-status.running { color: #2563eb; }
.execution-status.done { color: #047857; }
.execution-status.error { color: #b91c1c; }

.execution-log-list {
  display: grid;
  gap: 6px;
  padding: 8px 10px;
  border-top: 1px solid #e5e7eb;
}

.execution-log-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 5px 7px;
  align-items: center;
  color: #475569;
  font-size: 12px;
}

.execution-log-item .running { color: #2563eb; }
.execution-log-item .done { color: #059669; }
.execution-log-item .error { color: #dc2626; }

.execution-log-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.execution-log-detail {
  grid-column: 2;
  overflow: hidden;
  color: #94a3b8;
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
