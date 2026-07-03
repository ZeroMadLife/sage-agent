<script setup lang="ts">
import { Send } from 'lucide-vue-next'
import { ref } from 'vue'

const emit = defineEmits<{ submit: [content: string] }>()
const content = ref('周末去杭州2日游预算500元喜欢美食')

function submit() {
  const trimmed = content.value.trim()
  if (trimmed) {
    emit('submit', trimmed)
  }
}
</script>

<template>
  <form class="chat-composer" @submit.prevent="submit">
    <label for="travel-request">旅游需求</label>
    <textarea id="travel-request" v-model="content" rows="3" />
    <button type="submit">
      <Send :size="16" aria-hidden="true" />
      <span>开始规划</span>
    </button>
  </form>
</template>
