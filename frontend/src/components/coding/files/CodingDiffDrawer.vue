<script setup lang="ts">
import { ChevronDown, ChevronRight, X } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import type { CodingFileDiff, CodingRunDiff } from '../../../types/api'

const props = defineProps<{
  diff: CodingRunDiff | null
  visible: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const expandedFile = ref<string | null>(null)
const diffView = ref<'unified' | 'split'>('unified')

type DiffLine = {
  content: string
  kind: 'add' | 'remove' | 'context' | 'meta'
  oldNumber: number | null
  newNumber: number | null
}

// Reset expanded state whenever the drawer is hidden so reopening starts fresh.
watch(
  () => props.visible,
  (visible) => {
    if (!visible) {
      expandedFile.value = null
      diffView.value = 'unified'
    }
  },
)

function toggleFile(path: string) {
  expandedFile.value = expandedFile.value === path ? null : path
}

function parseDiff(content: string): DiffLine[] {
  let oldNumber = 1
  let newNumber = 1
  return content.split('\n').map((line) => {
    const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
    if (hunk) {
      oldNumber = Number(hunk[1])
      newNumber = Number(hunk[2])
      return { content: line, kind: 'meta', oldNumber: null, newNumber: null }
    }
    if (line.startsWith('---') || line.startsWith('+++')) {
      return { content: line, kind: 'meta', oldNumber: null, newNumber: null }
    }
    if (line.startsWith('+')) return { content: line.slice(1), kind: 'add', oldNumber: null, newNumber: newNumber++ }
    if (line.startsWith('-')) return { content: line.slice(1), kind: 'remove', oldNumber: oldNumber++, newNumber: null }
    if (line.startsWith(' ')) return { content: line.slice(1), kind: 'context', oldNumber: oldNumber++, newNumber: newNumber++ }
    return { content: line, kind: 'context', oldNumber: oldNumber++, newNumber: newNumber++ }
  })
}

const expandedDiffLines = computed(() => {
  const file = props.diff?.changed_files.find((item) => item.path === expandedFile.value)
  return file?.diff ? parseDiff(file.diff) : []
})

function lineClass(line: DiffLine) {
  return `diff-line ${line.kind}`
}

function statusLabel(status: CodingFileDiff['status']) {
  const labels: Record<CodingFileDiff['status'], string> = {
    added: '新增',
    modified: '修改',
    deleted: '删除',
  }
  return labels[status] || status
}

function statusClass(status: CodingFileDiff['status']) {
  return `status-${status}`
}

function fileTitle(file: CodingFileDiff): string {
  if (file.binary) return `${file.path} (二进制)`
  if (file.ignored_sensitive) return `${file.path} (含敏感内容,已隐藏)`
  return file.path
}
</script>

<template>
  <div v-if="visible" class="diff-backdrop" role="presentation" @click.self="emit('close')">
    <section
      class="diff-drawer"
      role="dialog"
      aria-modal="true"
      aria-label="运行变更"
    >
      <header class="diff-drawer-header">
        <div class="diff-drawer-title">
          <p class="eyebrow">运行变更</p>
          <h3>运行变更 ({{ diff?.file_count ?? 0 }} 个文件)</h3>
        </div>
        <button
          class="close-btn"
          type="button"
          aria-label="关闭变更面板"
          @click="emit('close')"
        >
          <X :size="16" />
        </button>
      </header>

      <div class="diff-drawer-body">
        <p v-if="diff?.truncated" class="diff-truncated-note">
          变更文件较多,部分文件未显示。
        </p>
        <p v-if="!diff || diff.changed_files.length === 0" class="diff-empty">
          暂无变更文件
        </p>

        <ul v-else class="file-list">
          <li v-for="file in diff.changed_files" :key="file.path" class="file-item">
            <button
              class="file-header"
              type="button"
              :disabled="file.binary || file.ignored_sensitive"
              :aria-expanded="expandedFile === file.path"
              @click="toggleFile(file.path)"
            >
              <component
                :is="expandedFile === file.path ? ChevronDown : ChevronRight"
                :size="13"
              />
              <span class="file-path">{{ fileTitle(file) }}</span>
              <span class="file-status" :class="statusClass(file.status)">
                {{ statusLabel(file.status) }}
              </span>
            </button>
            <div v-if="expandedFile === file.path" class="file-diff">
              <template v-if="file.diff">
                <div class="diff-view-toolbar" role="group" aria-label="Diff 视图">
                  <span>Diff 视图</span>
                  <button type="button" :class="{ active: diffView === 'unified' }" aria-label="切换为统一 Diff" :aria-pressed="diffView === 'unified'" @click="diffView = 'unified'">统一</button>
                  <button type="button" :class="{ active: diffView === 'split' }" aria-label="切换为并排 Diff" :aria-pressed="diffView === 'split'" @click="diffView = 'split'">并排</button>
                </div>
                <div v-if="diffView === 'unified'" class="diff-content" aria-label="统一 Diff">
                  <div v-for="(line, index) in expandedDiffLines" :key="index" :class="lineClass(line)">
                    <span class="diff-line-number">{{ line.oldNumber ?? '' }}</span><span class="diff-line-number">{{ line.newNumber ?? '' }}</span><code>{{ line.kind === 'add' ? '+' : line.kind === 'remove' ? '-' : ' ' }}{{ line.content }}</code>
                  </div>
                </div>
                <div v-else class="side-by-side-diff" aria-label="并排 Diff">
                  <div v-for="(line, index) in expandedDiffLines" :key="index" class="split-line">
                    <div :class="['split-cell', line.kind === 'remove' ? 'remove' : line.kind === 'context' ? 'context' : 'empty']"><span class="diff-line-number">{{ line.oldNumber ?? '' }}</span><code>{{ line.kind === 'remove' || line.kind === 'context' ? line.content : '' }}</code></div>
                    <div :class="['split-cell', line.kind === 'add' ? 'add' : line.kind === 'context' ? 'context' : 'empty']"><span class="diff-line-number">{{ line.newNumber ?? '' }}</span><code>{{ line.kind === 'add' || line.kind === 'context' ? line.content : '' }}</code></div>
                  </div>
                </div>
              </template>
              <p v-else class="diff-placeholder">无 diff 内容</p>
            </div>
          </li>
        </ul>
      </div>
    </section>
  </div>
</template>

<style scoped>
.diff-backdrop {
  position: fixed;
  inset: 0;
  z-index: 30;
  display: flex;
  justify-content: flex-end;
  background: rgb(17 18 20 / 42%);
}

.diff-drawer {
  display: grid;
  grid-template-rows: auto 1fr;
  width: min(560px, 100%);
  height: 100%;
  border-left: 1px solid var(--sage-border);
  background: var(--sage-surface);
  box-shadow: var(--sage-shadow-drawer);
}

.diff-drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--sage-border);
}

.diff-drawer-title .eyebrow {
  margin: 0 0 3px;
  color: var(--sage-text-muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

.diff-drawer-title h3 {
  margin: 0;
  color: var(--sage-text);
  font-size: 14px;
}

.close-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  min-height: 30px;
  padding: 0;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  background: var(--sage-surface);
  color: var(--sage-text-secondary);
  cursor: pointer;
}

.close-btn:hover {
  background: var(--sage-surface-muted);
}

.diff-drawer-body {
  overflow: auto;
  padding: 12px 14px;
  background: var(--sage-surface);
}

.diff-truncated-note {
  margin: 0 0 10px;
  padding: 6px 10px;
  border-radius: var(--sage-radius);
  background: var(--sage-warning-bg);
  color: var(--sage-warning);
  font-size: 12px;
}

.diff-empty {
  margin: 0;
  color: var(--sage-text-muted);
  font-size: 13px;
}

.file-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.file-item {
  margin-bottom: 2px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  background: var(--sage-surface);
}

.file-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.file-header:disabled {
  cursor: default;
  color: var(--sage-text-muted);
}

.file-header:hover:not(:disabled) {
  background: var(--sage-surface-muted);
  border-radius: var(--sage-radius);
}

.file-path {
  flex: 1;
  overflow: hidden;
  color: var(--sage-text);
  font-size: 12px;
  font-family: var(--sage-font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-status {
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 700;
  background: var(--sage-surface-muted);
  color: var(--sage-text-secondary);
}

.file-status.status-added {
  background: var(--sage-success-bg);
  color: var(--sage-success);
}

.file-status.status-modified {
  background: var(--sage-warning-bg);
  color: var(--sage-warning);
}

.file-status.status-deleted {
  background: var(--sage-danger-bg);
  color: var(--sage-danger);
}

.file-diff {
  border-top: 1px solid var(--sage-border);
  padding: 8px 10px;
  background: var(--sage-surface-raised);
}

.diff-view-toolbar { display:flex; align-items:center; gap:4px; margin:0 0 8px; color:var(--sage-text-muted); font-size:11px; }.diff-view-toolbar > span { margin-right:auto; }.diff-view-toolbar button { min-height:25px; border:1px solid var(--sage-border); border-radius:4px; padding:0 7px; color:var(--sage-text-muted); background:var(--sage-surface); font-size:11px; }.diff-view-toolbar button.active { border-color:var(--sage-text); color:var(--sage-text); background:var(--sage-surface-muted); }
.diff-content,.side-by-side-diff { max-height:420px; overflow:auto; border:1px solid var(--sage-border); background:var(--sage-code-bg); color:var(--sage-code-text); font:11px/1.55 var(--sage-font-mono); white-space:pre; }.diff-line { display:grid; grid-template-columns:42px 42px minmax(max-content,1fr); min-width:max-content; }.diff-line-number { display:block; min-width:0; padding:0 7px; border-right:1px solid color-mix(in srgb,var(--sage-border) 70%,transparent); color:var(--sage-code-muted); text-align:right; user-select:none; }.diff-line code,.split-cell code { padding:0 10px; color:inherit; font:inherit; }.diff-line.add,.split-cell.add { background:var(--sage-diff-add-bg); color:var(--sage-diff-add-text); }.diff-line.remove,.split-cell.remove { background:var(--sage-diff-remove-bg); color:var(--sage-diff-remove-text); }.diff-line.meta { color:var(--sage-code-muted); background:var(--sage-surface-muted); }.side-by-side-diff { min-width:max-content; }.split-line { display:grid; grid-template-columns:minmax(280px,1fr) minmax(280px,1fr); min-width:560px; }.split-cell { display:grid; grid-template-columns:42px minmax(max-content,1fr); min-height:18px; border-right:1px solid var(--sage-border); }.split-cell.context { color:var(--sage-code-text); }.split-cell.empty { color:transparent; background:var(--sage-surface-muted); }

.diff-placeholder {
  margin: 0;
  color: var(--sage-text-muted);
  font-size: 12px;
}
</style>
