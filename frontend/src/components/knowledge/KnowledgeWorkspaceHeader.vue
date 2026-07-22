<script setup lang="ts">
import { Menu, Network, RefreshCw, Upload } from 'lucide-vue-next'

withDefaults(defineProps<{
  workspaceName?: string
  connected?: boolean
  stale?: boolean
  syncing?: boolean
  graphRevision?: string
}>(), {
  workspaceName: '本地知识空间',
  connected: false,
  stale: false,
  syncing: false,
  graphRevision: '',
})

defineEmits<{
  openLibrary: []
  sync: []
  import: []
}>()
</script>

<template>
  <header class="knowledge-workspace-header">
    <div class="knowledge-workspace-identity">
      <button type="button" aria-label="打开知识来源" title="知识来源" @click="$emit('openLibrary')"><Menu :size="18" /></button>
      <span class="workspace-symbol"><Network :size="18" /></span>
      <span class="workspace-copy">
        <strong>{{ workspaceName }}</strong>
        <small><i :class="{ ready: connected }"></i>{{ stale ? '图谱待更新' : connected ? '来源与索引已连接' : '正在连接' }}</small>
      </span>
    </div>
    <div class="knowledge-workspace-actions">
      <code v-if="graphRevision">{{ graphRevision.slice(0, 16) }}</code>
      <button type="button" :disabled="syncing" aria-label="同步图谱" title="同步图谱" @click="$emit('sync')"><RefreshCw :size="16" :class="{ spinning: syncing }" /><span class="sr-only">同步图谱</span></button>
      <button class="import-action primary-button" type="button" @click="$emit('import')"><Upload :size="15" /><span>导入来源</span></button>
    </div>
  </header>
</template>

<style scoped>
.knowledge-workspace-header { display:flex; align-items:center; justify-content:space-between; gap:16px; min-width:0; min-height:58px; padding:0 12px; border-bottom:1px solid var(--sage-border); background:var(--sage-surface); }
.knowledge-workspace-identity,.knowledge-workspace-actions { display:flex; align-items:center; min-width:0; gap:8px; }
.knowledge-workspace-identity>button,.knowledge-workspace-actions>button:not(.import-action) { display:grid; place-items:center; width:34px; height:34px; padding:0; border:0; border-radius:var(--sage-radius); color:var(--sage-text-muted); background:transparent; }
.knowledge-workspace-identity>button:hover,.knowledge-workspace-actions>button:not(.import-action):hover { color:var(--sage-text); background:var(--sage-surface-muted); }
.workspace-symbol { display:grid; place-items:center; width:32px; height:32px; flex:none; border-radius:var(--sage-radius); color:var(--sage-brand-strong); background:var(--sage-brand-bg); }
.workspace-copy { display:grid; min-width:0; gap:1px; }.workspace-copy strong { max-width:280px; overflow:hidden; font-size:var(--sage-font-sm); text-overflow:ellipsis; white-space:nowrap; }.workspace-copy small { display:flex; align-items:center; gap:5px; color:var(--sage-text-muted); font-size:10px; }.workspace-copy i { width:6px; height:6px; border-radius:50%; background:var(--sage-border-strong); }.workspace-copy i.ready { background:var(--sage-success); }
.knowledge-workspace-actions code { max-width:150px; overflow:hidden; color:var(--sage-text-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.import-action { display:flex; align-items:center; justify-content:center; gap:6px; min-height:34px; padding:0 11px; border:1px solid var(--sage-brand-strong); border-radius:var(--sage-radius); color:white; background:var(--sage-brand-strong); font-size:var(--sage-font-xs); font-weight:650; }
.knowledge-workspace-actions button:disabled { cursor:not-allowed; opacity:.55; }
.spinning { animation:spin .9s linear infinite; }@keyframes spin { to { transform:rotate(360deg); } }
@media (max-width:620px) { .knowledge-workspace-header { padding-left:52px; }.workspace-symbol,.knowledge-workspace-actions code,.import-action span { display:none; }.workspace-copy strong { max-width:165px; }.import-action { width:34px; padding:0; } }
@media (prefers-reduced-motion:reduce) { .spinning { animation:none; } }
</style>
