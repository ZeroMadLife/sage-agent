<script setup lang="ts">
import type { ExecutionActivityViewModel } from '../../../harness/chatTypes'
import ChatExecutionLog from '../../harness/chat/ChatExecutionLog.vue'

defineProps<{
  activities: ExecutionActivityViewModel[]
  isThinking: boolean
}>()
</script>

<template>
  <ChatExecutionLog :activities="activities" :is-thinking="isThinking" />
</template>
