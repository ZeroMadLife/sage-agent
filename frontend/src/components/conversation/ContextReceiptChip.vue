<script setup lang="ts">
import { BookOpenText, X } from 'lucide-vue-next'
import { computed } from 'vue'
import type { HarnessSurfaceContext } from '../../harness/types'

const props = withDefaults(defineProps<{
  context: HarnessSurfaceContext
  state?: 'pending' | 'frozen'
  removable?: boolean
}>(), {
  state: 'pending',
  removable: false,
})

defineEmits<{ remove: [] }>()

const label = computed(() => (
  props.context.selection?.label
  || props.context.resource?.label
  || (props.context.surface === 'knowledge' ? 'Knowledge 上下文' : '当前上下文')
))

const receipt = computed(() => {
  const parts: string[] = []
  if (props.context.graphRevision) parts.push('已绑定图谱')
  if (props.context.selection) {
    parts.push(props.context.selection.type === 'graph_node' ? '已选节点' : '已选内容')
  }
  if (props.context.resource) {
    parts.push(props.context.resource.type === 'knowledge_page' ? '已绑定知识页' : '已绑定来源')
  }
  return parts.join(' · ')
})
</script>

<template>
  <div class="context-receipt-chip" :data-state="state">
    <BookOpenText :size="15" aria-hidden="true" />
    <span class="context-receipt-copy">
      <strong>{{ label }}</strong>
      <small v-if="receipt">{{ receipt }}</small>
    </span>
    <span class="context-receipt-state">{{ state === 'frozen' ? '已冻结' : '提交时冻结' }}</span>
    <button
      v-if="removable"
      type="button"
      :aria-label="`移除上下文 ${label}`"
      title="移除上下文"
      @click="$emit('remove')"
    ><X :size="14" /></button>
  </div>
</template>

<style scoped>
.context-receipt-chip {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 7px 9px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  color: var(--sage-source);
  background: var(--sage-source-bg);
}
.context-receipt-copy { display: grid; min-width: 0; gap: 2px; }
.context-receipt-copy strong,
.context-receipt-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.context-receipt-copy strong { color: var(--sage-text-secondary); font-size: var(--sage-font-xs); }
.context-receipt-copy small { color: var(--sage-text-muted); font-size: 9px; }
.context-receipt-state { color: var(--sage-source); font-size: 10px; white-space: nowrap; }
.context-receipt-chip[data-state="frozen"] { color: var(--sage-success); background: var(--sage-success-bg); }
.context-receipt-chip[data-state="frozen"] .context-receipt-state { color: var(--sage-success); }
.context-receipt-chip button { display: grid; place-items: center; width: 28px; height: 28px; padding: 0; border: 0; border-radius: var(--sage-radius-sm); color: var(--sage-text-muted); background: transparent; }
.context-receipt-chip button:hover { color: var(--sage-text); background: color-mix(in srgb, var(--sage-surface) 65%, transparent); }
@media (max-width: 520px) {
  .context-receipt-chip { grid-template-columns: 18px minmax(0, 1fr) auto; }
  .context-receipt-state { grid-column: 2; }
  .context-receipt-chip button { grid-column: 3; grid-row: 1 / 3; }
}
</style>
