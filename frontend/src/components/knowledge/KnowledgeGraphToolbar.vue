<script setup lang="ts">
import { CircleDot, Filter, Route, Search } from 'lucide-vue-next'
import type { KnowledgeGraphNodeKind } from '../../types/api'
import type { KnowledgeGraphScopeMode } from './knowledgeGraphPresentation'

defineProps<{
  query: string
  scope: KnowledgeGraphScopeMode
  depth: 1 | 2 | 3
  colorMode: 'type' | 'community'
  visibleKinds: KnowledgeGraphNodeKind[]
  canUseGoal: boolean
  canUseLocal: boolean
  canShowGoalPath: boolean
  showGoalPath: boolean
}>()

const emit = defineEmits<{
  'update:query': [value: string]
  'update:scope': [value: KnowledgeGraphScopeMode]
  'update:depth': [value: 1 | 2 | 3]
  'update:colorMode': [value: 'type' | 'community']
  toggleKind: [kind: KnowledgeGraphNodeKind]
  toggleGoalPath: []
}>()

const kinds: Array<{ id: KnowledgeGraphNodeKind; label: string }> = [
  { id: 'page', label: '页面' }, { id: 'source', label: '来源' }, { id: 'project', label: '项目' },
  { id: 'concept', label: '概念' }, { id: 'decision', label: '决策' }, { id: 'tool', label: '工具' },
]
</script>

<template>
  <div class="knowledge-graph-toolbar">
    <label class="graph-search"><Search :size="14" /><input :value="query" type="search" aria-label="搜索图谱节点" placeholder="搜索节点" @input="emit('update:query', ($event.target as HTMLInputElement).value)" /></label>
    <div class="scope-control" role="group" aria-label="图谱探索范围">
      <button type="button" :class="{ active: scope === 'global' }" :aria-pressed="scope === 'global'" @click="emit('update:scope', 'global')">全局</button>
      <button type="button" :class="{ active: scope === 'goal' }" :aria-pressed="scope === 'goal'" :disabled="!canUseGoal" @click="emit('update:scope', 'goal')">目标</button>
      <button type="button" :class="{ active: scope === 'local' }" :aria-pressed="scope === 'local'" :disabled="!canUseLocal" @click="emit('update:scope', 'local')">局部</button>
    </div>
    <label v-if="scope !== 'global'" class="compact-select">深度<select :value="depth" aria-label="图谱探索深度" @change="emit('update:depth', Number(($event.target as HTMLSelectElement).value) as 1 | 2 | 3)"><option :value="1">1</option><option :value="2">2</option><option :value="3">3</option></select></label>
    <span class="toolbar-spacer"></span>
    <button v-if="canUseLocal" class="path-action" :class="{ active: showGoalPath }" type="button" :disabled="!canShowGoalPath" :aria-pressed="showGoalPath" aria-label="显示到目标的路径" title="到目标的路径" @click="emit('toggleGoalPath')"><Route :size="15" /></button>
    <label class="compact-select color-select" title="节点着色方式"><CircleDot :size="14" /><select :value="colorMode" aria-label="图谱着色方式" @change="emit('update:colorMode', ($event.target as HTMLSelectElement).value as 'type' | 'community')"><option value="community">社区</option><option value="type">类型</option></select></label>
    <details class="kind-filter"><summary aria-label="筛选节点类型" title="筛选节点"><Filter :size="15" /></summary><div><label v-for="kind in kinds" :key="kind.id"><input type="checkbox" :checked="visibleKinds.includes(kind.id)" @change="emit('toggleKind', kind.id)" />{{ kind.label }}</label></div></details>
  </div>
</template>

<style scoped>
.knowledge-graph-toolbar { display:flex; align-items:center; gap:7px; min-width:0; min-height:48px; padding:7px 10px; border-bottom:1px solid var(--sage-border); background:color-mix(in srgb,var(--sage-surface) 96%,transparent); }
.graph-search { display:flex; align-items:center; gap:6px; width:min(220px,24vw); height:32px; padding:0 8px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-muted); background:var(--sage-surface-raised); }.graph-search:focus-within { border-color:var(--sage-source); }.graph-search input { min-width:0; width:100%; border:0; outline:0; color:var(--sage-text); background:transparent; font-size:var(--sage-font-xs); }
.scope-control { display:flex; height:32px; overflow:hidden; border:1px solid var(--sage-border); border-radius:var(--sage-radius); background:var(--sage-surface-muted); }.scope-control button { min-width:42px; padding:0 8px; border:0; border-left:1px solid var(--sage-border); color:var(--sage-text-muted); background:transparent; font-size:10px; }.scope-control button:first-child { border-left:0; }.scope-control button.active { color:var(--sage-brand-strong); background:var(--sage-surface); font-weight:700; }.scope-control button:disabled { opacity:.4; }
.toolbar-spacer { flex:1; }.compact-select { display:flex; align-items:center; gap:4px; height:32px; padding:0 7px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-muted); background:var(--sage-surface); font-size:10px; }.compact-select select { border:0; outline:0; color:var(--sage-text-secondary); background:transparent; font-size:10px; }
.path-action,.kind-filter summary { display:grid; place-items:center; width:32px; height:32px; padding:0; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-muted); background:var(--sage-surface); }.path-action.active { border-color:var(--sage-review-strong); color:var(--sage-review-strong); background:var(--sage-review-bg); }.path-action:disabled { opacity:.4; }
.kind-filter { position:relative; }.kind-filter summary { cursor:pointer; list-style:none; }.kind-filter>div { position:absolute; z-index:20; top:38px; right:0; display:grid; width:144px; padding:7px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); background:var(--sage-surface); box-shadow:var(--sage-shadow-sm); }.kind-filter label { display:flex; align-items:center; gap:7px; min-height:30px; color:var(--sage-text-secondary); font-size:10px; }.kind-filter input { accent-color:var(--sage-brand); }
@media (max-width:620px) { .knowledge-graph-toolbar { overflow-x:auto; padding:6px 8px; scrollbar-width:none; }.graph-search { width:126px; flex:none; }.color-select,.kind-filter { display:none; }.scope-control { flex:none; }.toolbar-spacer { display:none; } }
</style>
