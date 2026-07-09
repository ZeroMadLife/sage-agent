<script setup lang="ts">
import { ChevronRight, Folder, FileText } from 'lucide-vue-next'
import { useCodingStore } from '../../../stores/coding'

const store = useCodingStore()

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
  <aside class="file-tree">
    <div class="tree-header">
      <div class="breadcrumb">
        <button class="crumb" @click="navigateTo('.')">root</button>
        <template v-for="(part, i) in store.breadcrumb" :key="i">
          <ChevronRight :size="12" />
          <button class="crumb" @click="navigateTo(breadcrumbPath(i))">{{ part }}</button>
        </template>
      </div>
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
  background: #fafbfc;
  border-left: 1px solid #e5e7eb;
}

.tree-header {
  padding: 10px 12px;
  border-bottom: 1px solid #f0f1f3;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-wrap: wrap;
  font-size: 12px;
}

.crumb {
  border: 0;
  background: transparent;
  color: #2563eb;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 3px;
  font-size: 12px;
}

.crumb:hover {
  background: #eff6ff;
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
  color: #374151;
}

.tree-entry:hover {
  background: #f3f4f6;
}

.tree-entry.active {
  background: #dbeafe;
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
  border-top: 1px solid #e5e7eb;
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
  color: #4b5563;
  border-bottom: 1px solid #f0f1f3;
}

.preview-content {
  flex: 1;
  overflow: auto;
  margin: 0;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.5;
  font-family: 'SF Mono', 'Fira Code', monospace;
  white-space: pre-wrap;
  word-break: break-word;
  color: #1f2937;
}
</style>
