<script setup lang="ts">
import { Maximize2, X } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type { CodingApproval, CodingApprovalChoice } from '../../../types/api'

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

const approvalSummary = computed(() => {
  const content = props.approval.args.content
  if (props.approval.tool === 'write_file' && typeof content === 'string') {
    return `将写入 ${Math.max(1, content.split('\n').filter(Boolean).length)} 行到 ${diffPath.value}`
  }
  const command = props.approval.args.command
  if (props.approval.tool === 'run_shell' && typeof command === 'string') {
    return command
  }
  return JSON.stringify(compactArgs(props.approval.args), null, 2)
})

function compactArgs(args: Record<string, unknown>) {
  return Object.fromEntries(
    Object.entries(args).map(([key, value]) => [
      key,
      key === 'content' && typeof value === 'string' ? `${value.length} characters` : value,
    ]),
  )
}
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
            <Maximize2 :size="13" /> 查看差异
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
      <pre v-else>{{ approvalSummary }}</pre>
    </div>
    <div class="actions">
      <button class="deny" :disabled="busy" @click="emit('respond', 'deny')">拒绝</button>
      <button class="session" :disabled="busy" @click="emit('respond', 'session')">
        本会话允许
      </button>
      <button class="allow" :disabled="busy" @click="emit('respond', 'once')">允许一次</button>
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
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 14px;
  align-items: end;
  max-width: 760px;
  max-height: min(520px, calc(100vh - 132px));
  margin: 0 0 12px;
  padding: 12px;
  border: 1px solid color-mix(in srgb, var(--sage-warning) 56%, var(--sage-border));
  border-radius: var(--sage-radius-lg);
  background: var(--sage-warning-bg);
  box-shadow: var(--sage-shadow-drawer);
}

.approval-main {
  min-width: 0;
  overflow: auto;
}

.eyebrow {
  margin: 0 0 4px;
  color: var(--sage-warning);
  font-size: var(--sage-font-xs);
  font-weight: 700;
}

h2 {
  margin: 0 0 4px;
  color: var(--sage-text);
  font-size: 14px;
}

.description {
  margin: 0 0 8px;
  color: var(--sage-text-secondary);
  font-size: 13px;
}

pre {
  max-height: 110px;
  margin: 0;
  overflow: auto;
  color: var(--sage-text-secondary);
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  min-width: 0;
}

@media (max-width: 640px) {
  .approval-card {
    grid-template-columns: 1fr;
  }

  .actions {
    justify-content: flex-start;
  }
}

button {
  min-height: 32px;
  padding: 0 12px;
  border-radius: var(--sage-radius);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.deny {
  border: 1px solid var(--sage-border);
  color: var(--sage-text-secondary);
  background: var(--sage-surface);
}

.allow {
  border: 1px solid var(--sage-text);
  color: #fff;
  background: var(--sage-text);
}

.session {
  border: 1px solid var(--sage-border);
  color: var(--sage-text);
  background: var(--sage-surface);
}

.diff-preview {
  max-height: 150px;
  overflow: auto;
  border: 1px solid color-mix(in srgb, var(--sage-warning) 44%, var(--sage-border));
  border-radius: var(--sage-radius);
  background: var(--sage-surface);
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
  border-bottom: 1px solid color-mix(in srgb, var(--sage-warning) 44%, var(--sage-border));
  background: var(--sage-surface-raised);
  color: var(--sage-text-muted);
  font-family: Inter, system-ui, sans-serif;
  font-size: var(--sage-font-xs);
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
  border: 1px solid color-mix(in srgb, var(--sage-warning) 56%, var(--sage-border));
  color: var(--sage-warning);
  background: var(--sage-warning-bg);
}

.diff-line {
  display: grid;
  grid-template-columns: 18px 1fr;
  padding: 1px 6px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.diff-prefix {
  color: var(--sage-text-muted);
  user-select: none;
}

.diff-add {
  color: var(--sage-diff-add-text);
  background: var(--sage-diff-add-bg);
}

.diff-remove {
  color: var(--sage-diff-remove-text);
  background: var(--sage-diff-remove-bg);
}

.diff-context {
  color: var(--sage-text-secondary);
}

.diff-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--sage-overlay);
}

.diff-modal {
  display: grid;
  grid-template-rows: auto 1fr;
  width: min(920px, 100%);
  max-height: min(760px, 88vh);
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius-lg);
  background: var(--sage-surface);
  box-shadow: var(--sage-shadow-drawer);
}

.diff-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--sage-border);
}

.diff-modal-header p {
  margin: 0 0 3px;
  color: var(--sage-text-muted);
  font-size: var(--sage-font-xs);
  font-weight: 700;
  text-transform: uppercase;
}

.diff-modal-header h3 {
  margin: 0;
  color: var(--sage-text);
  font-size: 14px;
}

.close-diff {
  width: 30px;
  min-height: 30px;
  padding: 0;
  border: 1px solid var(--sage-border);
  background: var(--sage-surface);
}

.diff-modal-body {
  overflow: auto;
  background: var(--sage-surface-raised);
  font-family: 'SF Mono', monospace;
  font-size: 12px;
}

.diff-line-full {
  min-height: 22px;
  padding: 2px 10px;
}
</style>
