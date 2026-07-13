<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Sparkles, UserRound } from 'lucide-vue-next'
import type { ChatMessage } from '../../../stores/codingEvents'
import CodingExecutionLog from './CodingExecutionLog.vue'
import CodingToolActivity from './CodingToolActivity.vue'

withDefaults(defineProps<{
  message: ChatMessage
  renderedContent: string
  showProcess?: boolean
}>(), { showProcess: true })

const root = ref<HTMLElement | null>(null)
let copyTimer: ReturnType<typeof setTimeout> | undefined

async function copyCode(event: Event) {
  const button = event.target instanceof HTMLElement ? event.target.closest<HTMLButtonElement>('[data-copy-code]') : null
  if (!button || !root.value) return
  const block = button.closest<HTMLElement>('.sage-code-block')
  const code = block?.querySelector('code')?.textContent || ''
  if (!code || !navigator.clipboard?.writeText) return
  try {
    await navigator.clipboard.writeText(code)
  } catch {
    return
  }
  button.textContent = '已复制'
  button.dataset.copied = 'true'
  if (copyTimer) clearTimeout(copyTimer)
  copyTimer = setTimeout(() => {
    button.textContent = '复制'
    delete button.dataset.copied
  }, 1400)
}

onMounted(() => root.value?.addEventListener('click', copyCode))
onBeforeUnmount(() => {
  root.value?.removeEventListener('click', copyCode)
  if (copyTimer) clearTimeout(copyTimer)
})
</script>

<template>
  <section
    :class="['message-turn', message.role]"
    :data-turn-id="message.id"
    :data-run-id="message.run_id"
    ref="root"
  >
    <div
      class="message-avatar"
      :class="message.role"
      :aria-label="message.role === 'assistant' ? 'Sage' : '用户'"
    >
      <Sparkles v-if="message.role === 'assistant'" :size="15" />
      <UserRound v-else :size="15" />
    </div>

    <div class="message-body">
      <div v-if="message.role === 'user' || message.content" class="message-author">{{ message.role === 'assistant' ? 'Sage' : '你' }}</div>
      <CodingExecutionLog
        v-if="showProcess && message.activities && message.activities.length > 0"
        :activities="message.activities"
        :is-thinking="!!message.isThinking"
      />
      <div v-if="showProcess && message.tools && message.tools.length > 0" class="activity-row">
        <CodingToolActivity :tools="message.tools" :is-thinking="!!message.isThinking" />
      </div>
      <article v-if="message.content" class="message-content-shell">
        <div class="message-content" v-html="renderedContent"></div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.message-turn {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 12px;
  width: 100%;
  margin: 0 0 24px;
}

.message-turn.user {
  grid-template-columns: minmax(0, 820px) 30px;
  justify-content: end;
}

.message-turn.user .message-avatar {
  grid-column: 2;
  grid-row: 1;
}

.message-turn.user .message-body {
  grid-column: 1;
  grid-row: 1;
  align-items: flex-end;
}

.message-avatar {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  color: var(--sage-success);
  background: var(--sage-success-bg);
}

.message-avatar.user {
  color: var(--sage-text-secondary);
  background: var(--sage-surface-muted);
}

.message-body {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.message-author {
  margin: 2px 0 8px;
  color: var(--sage-text-muted);
  font-size: 11px;
  font-weight: 700;
}

.activity-row {
  width: 100%;
  margin: 0 0 4px;
}

.message-content-shell {
  min-width: 0;
  max-width: 100%;
  color: var(--sage-text);
  font-size: 14px;
  line-height: 1.72;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.message-turn.user .message-content-shell {
  padding: 10px 13px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius-lg);
  background: var(--sage-surface-muted);
}

.message-content :deep(p:first-child) {
  margin-top: 0;
}

.message-content :deep(p:last-child) {
  margin-bottom: 0;
}

.message-content :deep(p),
.message-content :deep(ul),
.message-content :deep(ol),
.message-content :deep(blockquote) {
  margin: 0 0 12px;
}

.message-content :deep(h1),
.message-content :deep(h2),
.message-content :deep(h3) {
  margin: 22px 0 10px;
  color: var(--sage-text);
  line-height: 1.35;
}

.message-content :deep(h1) { font-size: 20px; }
.message-content :deep(h2) { font-size: 17px; }
.message-content :deep(h3) { font-size: 15px; }
.message-content :deep(ul), .message-content :deep(ol) { padding-left: 22px; }
.message-content :deep(li + li) { margin-top: 4px; }
.message-content :deep(a) { color: var(--sage-success); text-underline-offset: 3px; }
.message-content :deep(blockquote) { padding-left: 12px; border-left: 2px solid var(--sage-border-strong); color: var(--sage-text-secondary); }

.message-content :deep(pre) {
  margin: 14px 0;
  padding: 14px 16px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  background: var(--sage-code-bg);
  color: var(--sage-code-text);
  overflow-x: auto;
  font-size: 12.5px;
  line-height: 1.65;
}

.message-content :deep(.sage-code-block) {
  margin: 14px 0;
  overflow: hidden;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  background: var(--sage-code-bg);
}

.message-content :deep(.code-block-header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 30px;
  padding: 0 10px;
  border-bottom: 1px solid var(--sage-border);
  color: var(--sage-text-muted);
  background: color-mix(in srgb, var(--sage-surface-raised) 75%, var(--sage-code-bg));
  font-family: var(--sage-font-mono);
  font-size: 10px;
  text-transform: lowercase;
}

.message-content :deep(.code-copy-button) {
  min-height: 22px;
  padding: 0 7px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius-sm);
  color: var(--sage-text-secondary);
  background: transparent;
  cursor: pointer;
  font-family: inherit;
  font-size: 10px;
}

.message-content :deep(.code-copy-button:hover),
.message-content :deep(.code-copy-button[data-copied="true"]) {
  border-color: var(--sage-border-strong);
  color: var(--sage-success);
}

.message-content :deep(.sage-code-block pre) {
  margin: 0;
  border: 0;
  border-radius: 0;
}

.message-content :deep(code) {
  font-family: var(--sage-font-mono);
}

.message-content :deep(:not(pre) > code) {
  padding: 2px 5px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius-sm);
  background: var(--sage-surface-muted);
  color: var(--sage-text-secondary);
  font-size: .92em;
}

@media (max-width: 640px) {
  .message-turn,
  .message-turn.user {
    grid-template-columns: 26px minmax(0, 1fr);
    gap: 8px;
  }

  .message-turn.user .message-avatar {
    grid-column: 1;
  }

  .message-turn.user .message-body {
    grid-column: 2;
    align-items: stretch;
  }

  .message-avatar {
    width: 26px;
    height: 26px;
  }
}
</style>
