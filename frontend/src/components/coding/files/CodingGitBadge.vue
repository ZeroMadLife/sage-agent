<script setup lang="ts">
import { GitBranch } from 'lucide-vue-next'
import { computed } from 'vue'
import { useCodingStore } from '../../../stores/coding'

const store = useCodingStore()
const label = computed(() => {
  if (!store.gitStatus.is_git) return '非 git 仓库'
  const dirty = store.gitStatus.dirty_count > 0 ? ` ●${store.gitStatus.dirty_count}` : ''
  return `${store.gitStatus.branch}${dirty}`
})
</script>

<template>
  <div class="git-badge" :class="{ disabled: !store.gitStatus.is_git }">
    <GitBranch :size="14" />
    <span>{{ label }}</span>
  </div>
</template>

<style scoped>
.git-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 999px;
  background: #f3f4f6;
  color: #374151;
  font-size: 12px;
  font-weight: 600;
}

.git-badge.disabled {
  color: #9ca3af;
}
</style>
