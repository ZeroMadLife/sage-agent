<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Terminal,
  Wrench,
  XCircle,
} from 'lucide-vue-next'
import type { ToolActivity } from '../../../stores/coding'

const props = defineProps<{
  tools: ToolActivity[]
  isThinking: boolean
}>()

const expanded = ref(false)
const keepOpen = ref(false)
const expandedResults = ref<Set<number>>(new Set())

const doneCount = computed(
  () => props.tools.filter((t) => t.status === 'done').length,
)
const errorCount = computed(
  () => props.tools.filter((t) => t.status === 'error').length,
)
const runningCount = computed(
  () => props.tools.filter((t) => t.status === 'running').length,
)

const allSettled = computed(
  () => !props.isThinking && runningCount.value === 0,
)
const isOpen = computed(() => expanded.value || keepOpen.value || props.isThinking)

watch(allSettled, async (settled) => {
  if (!settled) return
  keepOpen.value = true
  await nextTick()
  keepOpen.value = false
  expanded.value = false
})

function toggle() {
  expanded.value = !expanded.value
}

function toggleResult(index: number) {
  const next = new Set(expandedResults.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  expandedResults.value = next
}

function iconFor(tool: ToolActivity) {
  if (tool.status === 'running') return Circle
  if (tool.status === 'error') return XCircle
  return CheckCircle2
}

function colorFor(tool: ToolActivity) {
  if (tool.status === 'running') return '#3b82f6'
  if (tool.status === 'error') return '#ef4444'
  return '#10b981'
}

function resultPreview(content: string, expandedResult: boolean) {
  if (expandedResult || content.length <= 800) return content
  return `${smartTruncate(content, 800)}...`
}

function smartTruncate(content: string, limit: number) {
  const slice = content.slice(0, limit)
  const breakpoints = ['. ', '\n', '; ']
  const candidates = breakpoints
    .map((marker) => slice.lastIndexOf(marker))
    .filter((index) => index > limit * 0.55)
  const cut = candidates.length > 0 ? Math.max(...candidates) + 1 : limit
  return slice.slice(0, cut).trimEnd()
}

function lineClass(line: string) {
  if (line.startsWith('+') && !line.startsWith('+++')) return 'diff-add'
  if (line.startsWith('-') && !line.startsWith('---')) return 'diff-remove'
  return ''
}

function stringArg(args: Record<string, unknown>, key: string) {
  const value = args[key]
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function toolSummary(tool: ToolActivity) {
  const path = stringArg(tool.args, 'path')
  if (tool.tool === 'read_file') return `Read ${path || 'file'}`
  if (tool.tool === 'list_files') return `List ${path || '.'}`
  if (tool.tool === 'search') {
    const pattern = stringArg(tool.args, 'pattern')
    return `Search ${pattern || 'workspace'}${path ? ` in ${path}` : ''}`
  }
  if (tool.tool === 'write_file') return `Write ${path || 'file'}`
  if (tool.tool === 'patch_file') return `Patch ${path || 'file'}`
  if (tool.tool === 'run_shell') {
    const command = stringArg(tool.args, 'command')
    return `Run ${command || 'shell command'}`
  }
  if (tool.tool === 'todo_add') return `Add todo ${stringArg(tool.args, 'content')}`
  if (tool.tool === 'todo_update') return `Update todo ${stringArg(tool.args, 'id')}`
  if (tool.tool === 'todo_list') return 'List todos'
  if (tool.tool === 'enter_plan_mode') return `Enter plan mode ${stringArg(tool.args, 'topic')}`
  if (tool.tool === 'exit_plan_mode') return 'Exit plan mode'
  if (tool.tool === 'agent') return `Start agent ${stringArg(tool.args, 'task')}`
  return tool.tool.replaceAll('_', ' ')
}
</script>

<template>
  <div class="tool-activity" :class="{ settled: allSettled }">
    <button class="activity-header" @click="toggle">
      <component :is="isOpen ? ChevronDown : ChevronRight" :size="14" />
      <Wrench :size="13" />
      <span class="activity-label">
        Activity: {{ tools.length }} tool{{ tools.length > 1 ? 's' : '' }}
      </span>
      <span v-if="runningCount > 0" class="activity-badge running">
        {{ runningCount }} running
      </span>
      <span v-if="errorCount > 0" class="activity-badge error">
        {{ errorCount }} error
      </span>
      <span v-else-if="allSettled" class="activity-badge done">
        {{ doneCount }} done
      </span>
    </button>

    <div v-if="isOpen" class="tool-list">
      <div v-for="(tool, i) in tools" :key="i" class="tool-item">
        <div class="tool-row">
          <component :is="iconFor(tool)" :size="13" :color="colorFor(tool)" />
          <Terminal v-if="tool.tool === 'run_shell'" :size="12" />
          <span class="tool-name">{{ toolSummary(tool) }}</span>
          <span v-if="tool.status === 'running'" class="tool-spinner"></span>
        </div>
        <div v-if="tool.content" class="tool-result">
          <pre><span
            v-for="(line, lineIndex) in resultPreview(tool.content, expandedResults.has(i)).split('\n')"
            :key="lineIndex"
            :class="lineClass(line)"
          >{{ line }}{{ lineIndex < resultPreview(tool.content, expandedResults.has(i)).split('\n').length - 1 ? '\n' : '' }}</span></pre>
          <button
            v-if="tool.content.length > 800"
            class="show-more"
            @click="toggleResult(i)"
          >
            {{ expandedResults.has(i) ? 'Show less' : 'Show more' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tool-activity {
  margin: 4px 0 8px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #f9fafb;
  overflow: hidden;
}

.tool-activity.settled {
  opacity: 0.85;
}

.activity-header {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  border: 0;
  background: transparent;
  padding: 6px 10px;
  cursor: pointer;
  text-align: left;
  font-size: 12px;
  color: #4b5563;
}

.activity-header:hover {
  background: #f3f4f6;
}

.activity-label {
  font-weight: 600;
}

.activity-badge {
  margin-left: auto;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 600;
}

.activity-badge.running {
  background: #dbeafe;
  color: #1e40af;
}

.activity-badge.done {
  background: #d1fae5;
  color: #065f46;
}

.activity-badge.error {
  background: #fee2e2;
  color: #991b1b;
}

.tool-list {
  border-top: 1px solid #e5e7eb;
  padding: 4px 0;
}

.tool-item {
  padding: 4px 10px;
}

.tool-item:not(:last-child) {
  border-bottom: 1px solid #f3f4f6;
}

.tool-row {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
}

.tool-name {
  font-weight: 600;
  color: #374151;
}

.tool-args {
  color: #9ca3af;
  font-family: 'SF Mono', monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-spinner {
  width: 11px;
  height: 11px;
  border: 2px solid #dbeafe;
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.tool-result {
  margin-top: 3px;
  padding: 4px 8px;
  background: #fff;
  border-radius: 3px;
  border: 1px solid #f0f1f3;
}

.tool-result pre {
  margin: 0;
  font-size: 11px;
  line-height: 1.4;
  font-family: 'SF Mono', monospace;
  white-space: pre-wrap;
  word-break: break-word;
  color: #4b5563;
}

.diff-add {
  color: #047857;
  background: #ecfdf5;
}

.diff-remove {
  color: #b91c1c;
  background: #fef2f2;
}

.show-more {
  margin-top: 4px;
  border: 0;
  background: transparent;
  color: #2563eb;
  cursor: pointer;
  font-size: 11px;
  font-weight: 600;
}
</style>
