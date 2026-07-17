<script setup lang="ts">
import { ChevronRight, Folder, FileText, X } from 'lucide-vue-next'
import { useCodingStore } from '../../../stores/coding'

const store = useCodingStore()
const emit = defineEmits<{ close: [] }>()

function navigateTo(path: string) {
  store.loadFiles(path)
  store.previewPath = ''
  store.previewContent = ''
}

function toggleEntry(name: string, isDir: boolean) {
  if (isDir) {
    const newPath = store.fileTreePath === '.' ? name : `${store.fileTreePath}/${name}`
    store.loadFiles(newPath)
  } else {
    const filePath = store.fileTreePath === '.' ? name : `${store.fileTreePath}/${name}`
    store.loadFilePreview(filePath)
  }
}

function breadcrumbPath(index: number) {
  const parts = store.breadcrumb.slice(0, index + 1)
  return parts.join('/')
}
</script>

<template>
  <aside class="file-tree" aria-label="工作区文件">
    <div class="tree-header">
      <div class="breadcrumb">
        <button class="crumb" @click="navigateTo('.')">root</button>
        <template v-for="(part, i) in store.breadcrumb" :key="i">
          <ChevronRight :size="12" />
          <button class="crumb" @click="navigateTo(breadcrumbPath(i))">{{ part }}</button>
        </template>
      </div>
      <button class="close-files" type="button" aria-label="关闭文件面板" title="关闭文件面板" @click="emit('close')"><X :size="15" /></button>
    </div>

    <div class="tree-list">
      <button
        v-if="store.breadcrumb.length > 0"
        class="tree-entry"
        @click="navigateTo(store.breadcrumb.slice(0, -1).join('/') || '.')"
      >
        <span class="dir-icon">📁</span>
        <span class="entry-name">..</span>
      </button>
      <button
        v-for="entry in store.fileTreeEntries"
        :key="entry.name"
        class="tree-entry"
        :class="{ active: !entry.is_dir && store.previewPath.endsWith(entry.name) }"
        @click="toggleEntry(entry.name, entry.is_dir)"
      >
        <component :is="entry.is_dir ? Folder : FileText" :size="14" />
        <span class="entry-name">{{ entry.name }}</span>
      </button>
    </div>

    <div v-if="store.previewPath" class="preview">
      <div class="preview-header">
        <FileText :size="13" />
        <span>{{ store.previewPath }}</span>
      </div>
      <pre class="preview-content">{{ store.previewContent }}</pre>
    </div>
  </aside>
</template>

<style scoped>
.file-tree {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--sage-surface);
  border-left: 1px solid var(--sage-border);
}

.tree-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--sage-border);
}

.breadcrumb {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 2px;
  flex-wrap: wrap;
  font-size: 12px;
}

.close-files { display: inline-grid; place-items: center; flex: none; width: 28px; height: 28px; padding: 0; border: 1px solid var(--sage-border); border-radius: var(--sage-radius); color: var(--sage-text-secondary); background: var(--sage-surface); }
.close-files:hover { background: var(--sage-surface-muted); }

.crumb {
  border: 0;
  background: transparent;
  color: var(--sage-text-secondary);
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 3px;
  font-size: 12px;
}

.crumb:hover {
  background: var(--sage-surface-muted);
}

.tree-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px 8px;
}

.tree-entry {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  border: 0;
  background: transparent;
  padding: 3px 6px;
  border-radius: 4px;
  cursor: pointer;
  text-align: left;
  font-size: 13px;
  color: var(--sage-text-secondary);
}

.tree-entry:hover {
  background: var(--sage-surface-muted);
}

.tree-entry.active {
  background: var(--sage-surface-muted);
}

.dir-icon {
  font-size: 14px;
}

.entry-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview {
  border-top: 1px solid var(--sage-border);
  max-height: 45%;
  display: flex;
  flex-direction: column;
}

.preview-header {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 600;
  color: var(--sage-text-secondary);
  border-bottom: 1px solid var(--sage-border);
}

.preview-content {
  flex: 1;
  overflow: auto;
  margin: 0;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.5;
  font-family: var(--sage-font-mono);
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--sage-text);
}
</style>
