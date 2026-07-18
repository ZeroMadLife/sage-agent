<script setup lang="ts">
import { CheckCircle2, Circle, LoaderCircle, X, XCircle } from 'lucide-vue-next'
import type { CodingRunDetailResponse, CodingRunTimelineEntry } from '../../../types/api'

defineProps<{
  visible: boolean
  runId: string
  run: CodingRunDetailResponse | null
  error?: string
}>()

const emit = defineEmits<{ close: [] }>()

function statusIcon(status: string) {
  if (status === 'error') return XCircle
  if (status === 'running' || status === 'blocked') return LoaderCircle
  if (status === 'done' || status === 'completed') return CheckCircle2
  return Circle
}

function timeLabel(value: string) {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '--'
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function entryLabel(entry: CodingRunTimelineEntry) {
  if (entry.kind === 'model') return '模型'
  if (entry.kind === 'tool') return '调用工具'
  if (entry.kind === 'result') return '工具结果'
  if (entry.kind === 'final') return '回答'
  if (entry.kind === 'approval') return '审批'
  if (entry.kind === 'error') return '错误'
  return '运行'
}
</script>

<template>
  <div v-if="visible" class="run-backdrop" role="presentation" @click.self="emit('close')">
    <section class="run-drawer" role="dialog" aria-modal="true" aria-label="子代理运行详情">
      <header class="run-drawer-header">
        <div>
          <p>子代理审计</p>
          <h3>{{ runId }}</h3>
        </div>
        <button type="button" aria-label="关闭子代理运行详情" title="关闭" @click="emit('close')">
          <X :size="16" />
        </button>
      </header>

      <div class="run-drawer-body">
        <p v-if="error" class="run-error"><XCircle :size="16" />{{ error }}</p>
        <p v-else-if="!run" class="run-loading"><LoaderCircle :size="16" />正在读取运行记录...</p>
        <p v-else-if="!run.timeline.length" class="run-empty">该子运行尚未产生可展示事件。</p>
        <ol v-else class="run-timeline">
          <li v-for="(entry, index) in run.timeline" :key="`${entry.timestamp}:${entry.kind}:${index}`" :class="entry.status">
            <time>{{ timeLabel(entry.timestamp) }}</time>
            <span class="event-icon"><component :is="statusIcon(entry.status)" :size="14" /></span>
            <div>
              <small>{{ entryLabel(entry) }}</small>
              <strong>{{ entry.title }}</strong>
              <pre v-if="entry.detail">{{ entry.detail }}</pre>
            </div>
          </li>
        </ol>
      </div>
    </section>
  </div>
</template>

<style scoped>
.run-backdrop { position:fixed; z-index:34; inset:0; display:flex; justify-content:flex-end; background:rgb(17 18 20 / 42%); }
.run-drawer { display:grid; grid-template-rows:auto minmax(0,1fr); width:min(520px,100%); height:100%; border-left:1px solid var(--sage-border); color:var(--sage-text); background:var(--sage-surface); box-shadow:var(--sage-shadow-drawer); }
.run-drawer-header { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 14px; border-bottom:1px solid var(--sage-border); }.run-drawer-header p,.run-drawer-header h3 { margin:0; }.run-drawer-header p { color:var(--sage-text-muted); font-size:11px; font-weight:700; }.run-drawer-header h3 { max-width:390px; margin-top:3px; overflow:hidden; font-family:var(--sage-font-mono); font-size:13px; text-overflow:ellipsis; white-space:nowrap; }.run-drawer-header button { display:grid; place-items:center; width:30px; height:30px; padding:0; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:var(--sage-surface); }.run-drawer-header button:hover { background:var(--sage-surface-muted); }
.run-drawer-body { min-height:0; overflow:auto; }.run-loading,.run-empty,.run-error { margin:24px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.run-loading,.run-error { display:flex; align-items:center; gap:8px; }.run-loading svg { animation:run-spin 1s linear infinite; }.run-error { color:var(--sage-danger); }.run-timeline { margin:0; padding:8px 16px 28px; list-style:none; }.run-timeline li { display:grid; grid-template-columns:62px 20px minmax(0,1fr); gap:8px; padding:12px 0; border-bottom:1px solid var(--sage-border); }.run-timeline time { padding-top:2px; color:var(--sage-text-muted); font-family:var(--sage-font-mono); font-size:10px; }.event-icon { display:grid; place-items:start center; padding-top:1px; color:var(--sage-success); }.run-timeline li.error .event-icon { color:var(--sage-danger); }.run-timeline li.running .event-icon,.run-timeline li.blocked .event-icon { color:var(--sage-warning); }.run-timeline div { min-width:0; }.run-timeline small,.run-timeline strong { display:block; }.run-timeline small { color:var(--sage-text-muted); font-size:10px; }.run-timeline strong { margin-top:3px; font-size:var(--sage-font-xs); }.run-timeline pre { max-height:220px; margin:7px 0 0; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; color:var(--sage-text-secondary); font-family:var(--sage-font-mono); font-size:10px; line-height:1.55; }
@keyframes run-spin { to { transform:rotate(360deg); } }
@media (prefers-reduced-motion:reduce) { .run-loading svg { animation:none; } }
</style>
