<script setup lang="ts">
import type { Itinerary } from '../types/api'
import ItineraryCard from './ItineraryCard.vue'
import ToolCallStatusComponent from './ToolCallStatus.vue'

defineProps<{
  role: 'user' | 'assistant'
  content: string
  toolCalls?: Array<{ tool: string; args: Record<string, unknown>; status: string }>
  itinerary?: Itinerary | null
}>()
</script>

<template>
  <div class="message" :class="role">
    <div class="avatar">{{ role === 'user' ? '👤' : '🤖' }}</div>
    <div class="bubble">
      <ToolCallStatusComponent
        v-for="(call, i) in toolCalls"
        :key="i"
        :tool="call.tool"
        :status="call.status"
      />
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
</style>
