<script setup lang="ts">
import type { Itinerary } from '../types/api'
import ItineraryCard from './ItineraryCard.vue'
import ToolCallTrace from './ToolCallTrace.vue'

defineProps<{
  role: 'user' | 'assistant'
  content: string
  isThinking?: boolean
  statusMessage?: string
  toolCalls?: Array<{
    tool: string
    args: Record<string, unknown>
    status: 'running' | 'done' | 'error'
    message?: string
  }>
  itinerary?: Itinerary | null
}>()
</script>

<template>
  <div class="message" :class="role">
    <div class="avatar">{{ role === 'user' ? '👤' : '🤖' }}</div>
    <div class="bubble">
      <ToolCallTrace
        v-if="toolCalls && toolCalls.length > 0"
        :is-thinking="isThinking"
        :tool-calls="toolCalls"
      />
      <div v-if="role === 'assistant' && isThinking" class="thinking">
        <span class="thinking-dot" />
        <span>{{ statusMessage || '正在思考中...' }}</span>
      </div>
      <p v-if="content" class="content">{{ content }}</p>
      <ItineraryCard v-if="itinerary" :itinerary="itinerary" />
    </div>
  </div>
</template>

<style scoped>
.message {
  display: flex;
  gap: 0.75rem;
  margin-bottom: 1rem;
}
.message.user {
  flex-direction: row-reverse;
}
.avatar {
  font-size: 1.5rem;
  flex-shrink: 0;
}
.bubble {
  max-width: 70%;
  padding: 0.75rem 1rem;
  border-radius: 12px;
  background: #f9fafb;
}
.message.user .bubble {
  background: #dbeafe;
}
.content {
  margin: 0.5rem 0;
  white-space: pre-wrap;
  line-height: 1.5;
}
.thinking {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: #6b7280;
  font-size: 0.95rem;
  line-height: 1.5;
}
.thinking-dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 999px;
  background: #2563eb;
  animation: thinking-pulse 1.1s ease-in-out infinite;
}
@keyframes thinking-pulse {
  0%,
  100% {
    opacity: 0.35;
    transform: scale(0.8);
  }
  50% {
    opacity: 1;
    transform: scale(1);
  }
}
</style>
