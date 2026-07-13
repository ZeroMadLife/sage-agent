<script setup lang="ts">
import { X } from 'lucide-vue-next'
import CodingFileTree from './CodingFileTree.vue'

defineProps<{ visible: boolean }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <div v-if="visible" class="files-backdrop" role="presentation" @click.self="emit('close')">
    <section class="files-drawer" role="dialog" aria-modal="true" aria-label="工作区文件">
      <header class="files-drawer-header">
        <div><p>工作区</p><h3>文件</h3></div>
        <button type="button" aria-label="关闭文件面板" title="关闭文件面板" @click="emit('close')"><X :size="16" /></button>
      </header>
      <CodingFileTree @close="emit('close')" />
    </section>
  </div>
</template>

<style scoped>
.files-backdrop { position:fixed; inset:0; z-index:30; display:flex; justify-content:flex-end; background:rgb(17 18 20 / 42%); }
.files-drawer { display:grid; grid-template-rows:auto minmax(0,1fr); width:min(440px,100%); height:100%; border-left:1px solid var(--sage-border); background:var(--sage-surface); box-shadow:var(--sage-shadow-drawer); }
.files-drawer-header { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 14px; border-bottom:1px solid var(--sage-border); }.files-drawer-header p { margin:0 0 3px; color:var(--sage-text-muted); font-size:11px; font-weight:700; }.files-drawer-header h3 { margin:0; color:var(--sage-text); font-size:14px; }.files-drawer-header button { display:inline-grid; place-items:center; width:30px; height:30px; padding:0; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:var(--sage-surface); }.files-drawer-header button:hover { background:var(--sage-surface-muted); }.files-drawer :deep(.file-tree) { border:0; }
</style>
