<script setup lang="ts">
import { AlertTriangle, BookOpenText, History, Network, PanelLeftOpen } from 'lucide-vue-next'

type KnowledgeMode = 'graph' | 'wiki' | 'activity' | 'attention'

defineProps<{
  activeMode: KnowledgeMode
  attentionCount: number
}>()

defineEmits<{
  select: [mode: KnowledgeMode]
  openLibrary: []
}>()

const modes = [
  { id: 'graph' as const, label: '知识图谱', icon: Network },
  { id: 'wiki' as const, label: 'Wiki 页面', icon: BookOpenText },
  { id: 'activity' as const, label: '同步活动', icon: History },
  { id: 'attention' as const, label: '需要关注', icon: AlertTriangle },
]
</script>

<template>
  <aside class="knowledge-source-rail" aria-label="Knowledge 视图">
    <button type="button" aria-label="打开知识来源" title="知识来源" @click="$emit('openLibrary')"><PanelLeftOpen :size="17" /></button>
    <span class="rail-separator"></span>
    <button
      v-for="item in modes"
      :key="item.id"
      type="button"
      :data-mode="item.id"
      :class="{ active: activeMode === item.id }"
      :aria-label="item.label"
      :title="item.label"
      :aria-current="activeMode === item.id ? 'page' : undefined"
      @click="$emit('select', item.id)"
    ><component :is="item.icon" :size="17" /><em v-if="item.id === 'attention' && attentionCount">{{ attentionCount > 9 ? '9+' : attentionCount }}</em></button>
  </aside>
</template>

<style scoped>
.knowledge-source-rail { z-index:4; display:flex; align-items:center; flex-direction:column; gap:4px; min-width:0; min-height:0; padding:9px 7px; border-right:1px solid var(--sage-border); background:var(--sage-surface); }
.knowledge-source-rail button { position:relative; display:grid; place-items:center; width:36px; height:36px; padding:0; border:0; border-radius:var(--sage-radius); color:var(--sage-text-muted); background:transparent; }
.knowledge-source-rail button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }.knowledge-source-rail button.active { color:var(--sage-brand-strong); background:var(--sage-brand-bg); }
.knowledge-source-rail em { position:absolute; top:2px; right:2px; display:grid; place-items:center; min-width:14px; height:14px; padding:0 3px; border:2px solid var(--sage-surface); border-radius:7px; color:white; background:var(--sage-review-strong); font-size:8px; font-style:normal; }
.rail-separator { width:24px; height:1px; margin:2px 0; background:var(--sage-border); }
@media (max-width:899px) { .knowledge-source-rail { display:none; } }
</style>
