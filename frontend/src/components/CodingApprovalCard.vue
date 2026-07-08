<script setup lang="ts">
import { Maximize2, X } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type { CodingApproval, CodingApprovalChoice } from '../types/api'

const props = defineProps<{
  approval: CodingApproval
  busy?: boolean
}>()

const emit = defineEmits<{
  respond: [CodingApprovalChoice]
}>()

const showFullDiff = ref(false)
const diffPath = computed(() => {
  const path = props.approval.args.path
  return typeof path === 'string' && path.trim() ? path : props.approval.tool
})
</script>

<template>
  <section class="approval-card" aria-label="Tool approval">
    <div class="approval-main">
      <p class="eyebrow">需要确认</p>
      <h2>{{ approval.tool }}</h2>
      <p class="description">{{ approval.description }}</p>
      <div v-if="approval.diff_preview?.length" class="diff-preview">
        <div class="diff-preview-header">
          <span>{{ diffPath }}</span>
          <button class="view-diff" type="button" @click="showFullDiff = true">
            <Maximize2 :size="13" /> View diff
          </button>
        </div>
        <div
          v-for="(line, index) in approval.diff_preview"
          :key="index"
          :class="['diff-line', `diff-${line.type}`]"
        >
          <span class="diff-prefix">
            {{ line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' ' }}
          </span>
          <span>{{ line.text || ' ' }}</span>
        </div>
      </div>
      <pre v-else>{{ JSON.stringify(approval.args, null, 2) }}</pre>
    </div>
    <div class="actions">
      <button class="deny" :disabled="busy" @click="emit('respond', 'deny')">Deny</button>
      <button class="session" :disabled="busy" @click="emit('respond', 'session')">
        Allow session
      </button>
      <button class="always" :disabled="busy" @click="emit('respond', 'always')">
        Always
      </button>
      <button class="allow" :disabled="busy" @click="emit('respond', 'once')">Allow once</button>
    </div>

    <div
      v-if="showFullDiff && approval.diff_preview?.length"
      class="diff-modal-backdrop"
      role="presentation"
    >
      <section class="diff-modal" role="dialog" aria-modal="true" aria-label="Full diff review">
        <header class="diff-modal-header">
          <div>
            <p>Diff review</p>
            <h3>{{ diffPath }}</h3>
          </div>
          <button class="close-diff" type="button" aria-label="Close diff" @click="showFullDiff = false">
            <X :size="16" />
          </button>
        </header>
        <div class="diff-modal-body">
          <div
            v-for="(line, index) in approval.diff_preview"
            :key="`full-${index}`"
            :class="['diff-line', 'diff-line-full', `diff-${line.type}`]"
          >
            <span class="diff-prefix">
              {{ line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' ' }}
            </span>
            <span>{{ line.text || ' ' }}</span>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.approval-card {
  position: absolute;
  right: 18px;
  bottom: 96px;
  left: 18px;
  z-index: 5;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 14px;
  align-items: end;
  padding: 12px;
  border: 1px solid #f59e0b;
  border-radius: 8px;
  background: #fffbeb;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.16);
}

.approval-main {
  min-width: 0;
}

.eyebrow {
  margin: 0 0 4px;
  color: #92400e;
  font-size: 11px;
  font-weight: 700;
}

h2 {
  margin: 0 0 4px;
  color: #111827;
  font-size: 14px;
}

.description {
  margin: 0 0 8px;
  color: #374151;
  font-size: 13px;
}

pre {
  max-height: 110px;
  margin: 0;
  overflow: auto;
  color: #4b5563;
  font-size: 12px;
  white-space: pre-wrap;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

button {
  min-height: 32px;
  padding: 0 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.deny {
  border: 1px solid #d1d5db;
  color: #374151;
  background: #fff;
}

.allow {
  border: 1px solid #111827;
  color: #fff;
  background: #111827;
}

.session,
.always {
  border: 1px solid #d1d5db;
  color: #111827;
  background: #fff;
}

.diff-preview {
  max-height: 150px;
  overflow: auto;
  border: 1px solid #f0d9a4;
  border-radius: 6px;
  background: #fff;
  font-family: 'SF Mono', monospace;
  font-size: 12px;
}

.diff-preview-header {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: space-between;
  padding: 5px 6px;
  border-bottom: 1px solid #f0d9a4;
  background: #fffdf5;
  color: #6b7280;
  font-family: Inter, system-ui, sans-serif;
  font-size: 11px;
}

.view-diff,
.close-diff {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.view-diff {
  gap: 4px;
  min-height: 24px;
  padding: 0 7px;
  border: 1px solid #e5c77e;
  color: #78350f;
  background: #fffbeb;
}

.diff-line {
  display: grid;
  grid-template-columns: 18px 1fr;
  padding: 1px 6px;
  white-space: pre-wrap;
}

.diff-prefix {
  color: #6b7280;
  user-select: none;
}

.diff-add {
  color: #047857;
  background: #ecfdf5;
}

.diff-remove {
  color: #b91c1c;
  background: #fef2f2;
}

.diff-context {
  color: #4b5563;
}

.diff-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(17, 24, 39, 0.36);
}

.diff-modal {
  display: grid;
  grid-template-rows: auto 1fr;
  width: min(920px, 100%);
  max-height: min(760px, 88vh);
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.24);
}

.diff-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border-bottom: 1px solid #e5e7eb;
}

.diff-modal-header p {
  margin: 0 0 3px;
  color: #6b7280;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

.diff-modal-header h3 {
  margin: 0;
  color: #111827;
  font-size: 14px;
}

.close-diff {
  width: 30px;
  min-height: 30px;
  padding: 0;
  border: 1px solid #d1d5db;
  background: #fff;
}

.diff-modal-body {
  overflow: auto;
  background: #f9fafb;
  font-family: 'SF Mono', monospace;
  font-size: 12px;
}

.diff-line-full {
  min-height: 22px;
  padding: 2px 10px;
}
</style>
