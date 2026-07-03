<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ disabled: boolean }>()
const emit = defineEmits<{ submit: [content: string] }>()
const input = ref('')

function handleSubmit() {
  const content = input.value.trim()
  if (!content || props.disabled) return
  emit('submit', content)
  input.value = ''
}
</script>

<template>
  <div class="chat-input">
    <input
      v-model="input"
      :disabled="disabled"
      type="text"
      placeholder="输入旅游需求或问题..."
      @keyup.enter="handleSubmit"
    />
    <button :disabled="disabled || !input.trim()" @click="handleSubmit">
      {{ disabled ? '思考中...' : '发送' }}
    </button>
  </div>
</template>

<style scoped>
.chat-input {
  display: flex;
  gap: 0.5rem;
  padding: 1rem;
  border-top: 1px solid #e0e0e0;
}
input {
  flex: 1;
  padding: 0.5rem 1rem;
  border: 1px solid #d0d0d0;
  border-radius: 8px;
  font-size: 1rem;
  outline: none;
}
input:focus {
  border-color: #2563eb;
}
input:disabled {
  background: #f5f5f5;
  cursor: not-allowed;
}
button {
  padding: 0.5rem 1.5rem;
  border: none;
  border-radius: 8px;
  background: #2563eb;
  color: white;
  cursor: pointer;
  font-size: 1rem;
  white-space: nowrap;
}
button:disabled {
  background: #9ca3af;
  cursor: not-allowed;
}
</style>
