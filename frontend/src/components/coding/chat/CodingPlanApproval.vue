<script setup lang="ts">
import { ref } from 'vue'
import { useCodingStore } from '../../../stores/coding'
import { useMarkdown } from '../../../composables/useMarkdown'

const store = useCodingStore()
const { render } = useMarkdown()

// Guard against double-clicks while an approve/reject request is in flight.
const busy = ref(false)

async function onApprove() {
  if (busy.value) return
  busy.value = true
  try {
    await store.approvePlan()
  } finally {
    busy.value = false
  }
}

async function onReject() {
  if (busy.value) return
  busy.value = true
  try {
    await store.rejectPlan()
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="store.planReview" class="plan-approval" aria-label="Plan approval">
    <div class="plan-approval-header">
      <h3>📋 计划待审批</h3>
      <span class="plan-path">{{ store.planReview.plan_path }}</span>
    </div>
    <div class="plan-summary" v-html="render(store.planReview.summary)"></div>
    <div class="plan-actions">
      <button class="approve-btn" :disabled="busy" @click="onApprove">采纳并执行</button>
      <button class="reject-btn" :disabled="busy" @click="onReject">继续修改</button>
    </div>
  </div>
</template>

<style scoped>
.plan-approval {
  max-width: 760px;
  margin: 0 0 12px;
  padding: 12px 14px;
  border: 1px solid #16a34a;
  border-radius: 8px;
  background: #f0fdf4;
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
  font-size: 14px;
}

.plan-approval-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.plan-approval-header h3 {
  margin: 0;
  color: #14532d;
  font-size: 14px;
  font-weight: 700;
}

.plan-path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #4b5563;
  font-size: 12px;
  font-family: 'SF Mono', 'Fira Code', monospace;
}

.plan-summary {
  max-height: 320px;
  overflow: auto;
  margin-bottom: 10px;
  padding: 8px 10px;
  border: 1px solid #bbf7d0;
  border-radius: 6px;
  background: #fff;
  line-height: 1.6;
  color: #374151;
}

.plan-summary :deep(pre) {
  background: #f3f4f6;
  padding: 8px 12px;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 13px;
}

.plan-summary :deep(code) {
  font-family: 'SF Mono', 'Fira Code', monospace;
}

.plan-summary :deep(h1),
.plan-summary :deep(h2),
.plan-summary :deep(h3) {
  color: #111827;
}

.plan-summary :deep(ul),
.plan-summary :deep(ol) {
  padding-left: 22px;
}

.plan-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.plan-actions button {
  min-height: 32px;
  padding: 0 14px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.plan-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.approve-btn {
  border: 1px solid #15803d;
  color: #fff;
  background: #16a34a;
}

.approve-btn:hover:not(:disabled) {
  background: #15803d;
}

.reject-btn {
  border: 1px solid #d1d5db;
  color: #374151;
  background: #fff;
}

.reject-btn:hover:not(:disabled) {
  background: #f3f4f6;
}
</style>
