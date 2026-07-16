<script setup lang="ts">
import type { ChatMessageViewModel } from '../../../harness/chatTypes'
import ChatMessageTurn from '../../harness/chat/ChatMessageTurn.vue'

withDefaults(defineProps<{
  message: ChatMessageViewModel
  renderedContent: string
  showProcess?: boolean
  diffFileCount?: number
}>(), { showProcess: true, diffFileCount: 0 })

const emit = defineEmits<{ viewDiff: [] }>()
</script>

<template>
  <ChatMessageTurn
    :message="message"
    :rendered-content="renderedContent"
    :show-process="showProcess"
    :diff-file-count="diffFileCount"
    @view-diff="emit('viewDiff')"
  />
</template>
