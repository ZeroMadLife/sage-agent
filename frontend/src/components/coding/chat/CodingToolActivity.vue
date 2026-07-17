<script setup lang="ts">
import type { ToolActivityViewModel } from '../../../harness/chatTypes'
import ChatToolActivity from '../../harness/chat/ChatToolActivity.vue'

defineProps<{
  tools: ToolActivityViewModel[]
  isThinking: boolean
}>()
</script>

<template>
  <ChatToolActivity :tools="tools" :is-thinking="isThinking" />
</template>
