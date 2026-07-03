<script setup lang="ts">
defineProps<{ tool: string; status: string }>()
</script>

<template>
  <div class="tool-call">
    <span class="icon">🔧</span>
    <span class="name">{{ tool }}</span>
    <span class="status" :class="status">{{ status }}</span>
  </div>
</template>

<style scoped>
.tool-call {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.25rem 0.5rem;
  background: #f3f4f6;
  border-radius: 4px;
  font-size: 0.85rem;
  color: #6b7280;
  margin: 0.25rem 0;
}
.status.running { color: #f59e0b; }
.status.done { color: #10b981; }
.status.error { color: #ef4444; }
</style>
