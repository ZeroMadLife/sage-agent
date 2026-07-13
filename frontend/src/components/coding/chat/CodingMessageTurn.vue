<script setup lang="ts">
import { Sparkles, UserRound } from 'lucide-vue-next'
import type { ChatMessage } from '../../../stores/codingEvents'
import CodingExecutionLog from './CodingExecutionLog.vue'
import CodingToolActivity from './CodingToolActivity.vue'

withDefaults(defineProps<{
  message: ChatMessage
  renderedContent: string
  showProcess?: boolean
}>(), { showProcess: true })
</script>

<template>
  <section
    :class="['message-turn', message.role]"
    :data-turn-id="message.id"
    :data-run-id="message.run_id"
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
      <div class="message-author">{{ message.role === 'assistant' ? 'Sage' : '你' }}</div>
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
  grid-template-columns: 30px minmax(0, 760px);
  gap: 10px;
  width: 100%;
  margin: 0 0 18px;
}

.message-turn.user {
  grid-template-columns: minmax(0, 620px) 30px;
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
  width: 30px;
  height: 30px;
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
  margin: 0 0 5px;
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
  line-height: 1.65;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.message-turn.user .message-content-shell {
  padding: 9px 12px;
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

.message-content :deep(pre) {
  padding: 8px 12px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  background: var(--sage-code-bg);
  color: var(--sage-code-text);
  overflow-x: auto;
  font-size: 13px;
}

.message-content :deep(code) {
  font-family: 'SF Mono', 'Fira Code', monospace;
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
