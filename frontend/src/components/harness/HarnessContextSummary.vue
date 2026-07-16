<script setup lang="ts">
import { BookOpenText, Boxes, Clock3, Link2, LockKeyhole, MessageSquareText } from 'lucide-vue-next'
import { computed } from 'vue'
import type { HarnessSurfaceContext } from '../../harness/types'

const props = defineProps<{
  context: HarnessSurfaceContext
  operationLabels?: Record<string, string>
}>()

const subject = computed(() => props.context.selection ?? props.context.resource)

function shortRevision(value?: string) {
  if (!value) return '当前版本'
  return value.length > 18 ? `${value.slice(0, 18)}...` : value
}
</script>

<template>
  <section class="context-summary" aria-label="当前对话上下文">
    <header>
      <span class="context-mark"><MessageSquareText :size="17" /></span>
      <div><strong>知识对话</strong><small>{{ context.workspaceId || '本地知识空间' }}</small></div>
      <span class="context-state"><LockKeyhole :size="12" />已绑定</span>
    </header>

    <div v-if="subject" class="bound-context">
      <div class="context-icon"><BookOpenText :size="18" /></div>
      <div>
        <small>{{ subject.type === 'graph_node' ? '图谱节点' : '知识资源' }}</small>
        <strong>{{ subject.label || subject.id }}</strong>
        <span><Link2 :size="12" />{{ shortRevision(subject.revision) }}</span>
      </div>
    </div>
    <div v-else class="context-empty">
      <Boxes :size="24" />
      <strong>尚未选择知识内容</strong>
      <span>当前对话使用工作区级上下文</span>
    </div>

    <div v-if="context.operationRefs.length" class="operation-list" aria-label="关联任务">
      <div class="section-label"><Clock3 :size="13" />进行中的任务</div>
      <article v-for="operation in context.operationRefs" :key="`${operation.kind}:${operation.id}`">
        <span class="operation-dot"></span>
        <div>
          <strong>{{ operationLabels?.[operation.id] || '知识同步' }}</strong>
          <code>{{ operation.id }}</code>
        </div>
      </article>
    </div>

    <div class="chat-empty-state">
      <MessageSquareText :size="22" />
      <span>尚无对话</span>
    </div>
  </section>
</template>

<style scoped>
.context-summary { display:grid; grid-template-rows:auto auto auto minmax(92px,1fr); min-height:100%; color:var(--sage-text); background:var(--sage-surface); }
.context-summary>header { display:flex; align-items:center; gap:9px; min-height:58px; padding:0 14px; border-bottom:1px solid var(--sage-border); }.context-mark { display:grid; place-items:center; width:30px; height:30px; border-radius:var(--sage-radius); color:var(--sage-brand-strong); background:var(--sage-brand-bg); }.context-summary header div { display:grid; min-width:0; }.context-summary header strong { font-size:var(--sage-font-sm); }.context-summary header small { overflow:hidden; color:var(--sage-text-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }.context-state { display:inline-flex; align-items:center; gap:4px; margin-left:auto; color:var(--sage-success); font-size:10px; font-weight:700; }
.bound-context { display:grid; grid-template-columns:34px minmax(0,1fr); gap:10px; margin:14px; padding:12px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); background:var(--sage-surface-muted); }.context-icon { display:grid; place-items:center; width:34px; height:34px; color:var(--sage-source); }.bound-context>div:last-child { display:grid; gap:3px; min-width:0; }.bound-context small { color:var(--sage-text-muted); font-size:10px; }.bound-context strong { overflow:hidden; font-size:var(--sage-font-sm); text-overflow:ellipsis; white-space:nowrap; }.bound-context span { display:flex; align-items:center; gap:4px; overflow:hidden; color:var(--sage-text-muted); font-family:var(--sage-font-mono); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.context-empty { display:grid; justify-items:center; gap:5px; margin:14px; padding:22px 12px; border:1px dashed var(--sage-border-strong); border-radius:var(--sage-radius); color:var(--sage-text-muted); text-align:center; }.context-empty strong { color:var(--sage-text-secondary); font-size:var(--sage-font-sm); }.context-empty span { font-size:var(--sage-font-xs); }
.operation-list { display:grid; gap:6px; padding:0 14px 12px; }.section-label { display:flex; align-items:center; gap:5px; color:var(--sage-text-muted); font-size:10px; font-weight:700; }.operation-list article { display:grid; grid-template-columns:8px minmax(0,1fr); align-items:center; gap:8px; min-height:42px; padding:6px 9px; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); }.operation-dot { width:7px; height:7px; border-radius:50%; background:var(--sage-review); }.operation-list article div { display:grid; min-width:0; }.operation-list strong { font-size:var(--sage-font-xs); }.operation-list code { overflow:hidden; color:var(--sage-text-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.chat-empty-state { display:grid; place-content:center; justify-items:center; gap:7px; min-height:92px; border-top:1px solid var(--sage-border); color:var(--sage-text-muted); font-size:var(--sage-font-xs); }
</style>
