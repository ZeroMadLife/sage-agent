<script setup lang="ts">
import { computed, ref } from 'vue'
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
const isOpen = computed(() => expanded.value)

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
  if (tool.status === 'running') return 'var(--sage-warning)'
  if (tool.status === 'error') return 'var(--sage-danger)'
  return 'var(--sage-success)'
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

function formatArgs(args: Record<string, unknown>) {
  return JSON.stringify(args, null, 2)
}
</script>

<template>
  <div class="tool-activity" :class="{ settled: allSettled }">
    <button class="activity-header" @click="toggle">
      <component :is="isOpen ? ChevronDown : ChevronRight" :size="14" />
      <Wrench :size="13" />
      <span class="activity-label">
        工具调用 {{ tools.length }} 项
      </span>
      <span v-if="runningCount > 0" class="activity-badge running">
        {{ runningCount }} 执行中
      </span>
      <span v-if="errorCount > 0" class="activity-badge error">
        {{ errorCount }} 失败
      </span>
      <span v-else-if="allSettled" class="activity-badge done">
        {{ doneCount }} 已完成
      </span>
    </button>

    <div v-if="isOpen" class="tool-list">
      <div v-for="(tool, i) in tools" :key="i" class="tool-item">
        <div class="tool-row">
          <component :is="iconFor(tool)" :size="13" :color="colorFor(tool)" />
          <Terminal v-if="tool.tool === 'run_shell'" :size="12" />
          <span class="tool-name">{{ toolSummary(tool) }}</span>
          <span
            v-if="tool.status === 'running'"
            class="tool-spinner"
            style="--tool-spinner-color: var(--sage-warning)"
          ></span>
        </div>
        <details class="tool-args-details">
          <summary>参数</summary>
          <pre>{{ formatArgs(tool.args) }}</pre>
        </details>
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
            {{ expandedResults.has(i) ? '收起输出' : '展开输出' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tool-activity {
  margin: 4px 0 8px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  background: var(--sage-surface-raised);
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
  color: var(--sage-text-secondary);
}

.activity-header:hover {
  background: var(--sage-surface-muted);
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
  background: var(--sage-surface-muted);
  color: var(--sage-text-secondary);
}

.activity-badge.done {
  background: var(--sage-success-bg);
  color: var(--sage-success);
}

.activity-badge.error {
  background: var(--sage-danger-bg);
  color: var(--sage-danger);
}

.tool-list {
  border-top: 1px solid var(--sage-border);
  padding: 4px 0;
}

.tool-item {
  padding: 4px 10px;
}

.tool-item:not(:last-child) {
  border-bottom: 1px solid var(--sage-border);
}

.tool-row {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
}

.tool-name {
  font-weight: 600;
  color: var(--sage-text-secondary);
}

.tool-args-details { margin-top:4px; color:var(--sage-text-muted); font-size:11px; }.tool-args-details summary { width:max-content; cursor:pointer; }.tool-args-details pre { max-height:180px; margin:4px 0 0; padding:5px 7px; overflow:auto; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); background:var(--sage-surface); color:var(--sage-text-secondary); font-family:var(--sage-font-mono); font-size:11px; line-height:1.4; white-space:pre-wrap; word-break:break-word; }

.tool-spinner {
  width: 11px;
  height: 11px;
  border: 2px solid var(--sage-surface-muted);
  border-top-color: var(--tool-spinner-color, var(--sage-warning));
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
  background: var(--sage-surface);
  border-radius: var(--sage-radius-sm);
  border: 1px solid var(--sage-border);
}

.tool-result pre {
  margin: 0;
  font-size: 11px;
  line-height: 1.4;
  font-family: var(--sage-font-mono);
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--sage-text-secondary);
}

.diff-add {
  color: var(--sage-diff-add-text);
  background: var(--sage-diff-add-bg);
}

.diff-remove {
  color: var(--sage-diff-remove-text);
  background: var(--sage-diff-remove-bg);
}

.show-more {
  margin-top: 4px;
  border: 0;
  background: transparent;
  color: var(--sage-text-secondary);
  cursor: pointer;
  font-size: 11px;
  font-weight: 600;
}
</style>
