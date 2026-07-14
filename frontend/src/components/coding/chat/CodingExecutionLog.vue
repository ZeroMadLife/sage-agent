<script setup lang="ts">
import { CheckCircle2, ChevronDown, ChevronRight, Circle, History, RotateCcw, Wrench, XCircle } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type { ExecutionActivity } from '../../../stores/codingEvents'

const props = defineProps<{
  activities: ExecutionActivity[]
  isThinking: boolean
}>()

const expanded = ref(false)
const visibleActivities = computed(() => props.activities.filter((item) => item.kind !== 'model'))
const runningCount = computed(() => visibleActivities.value.filter((item) => item.status === 'running').length)
const errorCount = computed(() => visibleActivities.value.filter((item) => item.status === 'error').length)
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
  <section v-if="visibleActivities.length" class="execution-log" :class="{ open: isOpen }">
    <button class="execution-log-header" type="button" @click="toggle">
      <component :is="isOpen ? ChevronDown : ChevronRight" :size="14" />
      <History :size="13" />
      <span class="execution-log-title">执行过程</span>
      <span v-if="runningCount" class="execution-status running">进行中</span>
      <span v-else-if="errorCount" class="execution-status error">有错误</span>
      <span v-else class="execution-status done">已完成</span>
    </button>
    <div v-if="isOpen" class="execution-log-list">
      <div v-for="(activity, index) in visibleActivities" :key="index" class="execution-log-item">
        <component :is="iconFor(activity)" :size="13" :class="activity.status" />
        <span class="execution-log-label">{{ activity.label }}</span>
        <span v-if="activity.detail" class="execution-log-detail">{{ activity.detail }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.execution-log {
  width: 100%;
  margin: 0 0 5px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  background: var(--sage-surface-raised);
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
  color: var(--sage-text-secondary);
  cursor: pointer;
  font-size: 12px;
  text-align: left;
}

.execution-log-header:hover {
  background: var(--sage-surface-muted);
}

.execution-log-title {
  font-weight: 600;
}

.execution-status {
  margin-left: auto;
  font-size: 11px;
}

.execution-status.running { color: var(--sage-warning); }
.execution-status.done { color: var(--sage-success); }
.execution-status.error { color: var(--sage-danger); }

.execution-log-list {
  display: grid;
  gap: 6px;
  padding: 8px 10px;
  border-top: 1px solid var(--sage-border);
}

.execution-log-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 5px 7px;
  align-items: center;
  color: var(--sage-text-secondary);
  font-size: 12px;
}

.execution-log-item .running { color: var(--sage-warning); }
.execution-log-item .done { color: var(--sage-success); }
.execution-log-item .error { color: var(--sage-danger); }

.execution-log-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.execution-log-detail {
  grid-column: 2;
  overflow: hidden;
  color: var(--sage-text-muted);
  font-family: var(--sage-font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
