<script setup lang="ts">
import { BookOpenCheck, Dumbbell, FileSearch, ScanSearch } from 'lucide-vue-next'
import type {
  KnowledgeNodeResearchIntent,
  KnowledgeNodeResearchModel,
} from '../../harness/knowledgeNodeResearch'

defineProps<{
  model: KnowledgeNodeResearchModel
  loading: boolean
}>()

const emit = defineEmits<{
  choose: [intent: KnowledgeNodeResearchIntent]
}>()

function shortRevision(value: string | null) {
  if (!value) return ''
  return value.length > 12 ? `${value.slice(0, 12)}...` : value
}
</script>

<template>
  <section class="node-research-panel" aria-labelledby="node-research-title">
    <header>
      <span class="research-icon"><ScanSearch :size="17" /></span>
      <div>
        <small>节点研究 · 待提交</small>
        <strong id="node-research-title" :title="model.label">研究「{{ model.label }}」</strong>
      </div>
      <em :class="{ limited: !model.evidenceBound }">
        {{ model.evidenceBound ? 'revision 已绑定' : '证据待补' }}
      </em>
    </header>

    <p class="research-boundary">
      <span>提交上下文</span>
      <code v-if="model.graphRevision">graph {{ shortRevision(model.graphRevision) }}</code>
      <code v-if="model.pageRevision">page {{ shortRevision(model.pageRevision) }}</code>
      <code v-if="model.sourceRevision">source {{ shortRevision(model.sourceRevision) }}</code>
      <span v-if="loading">邻域读取中</span>
      <span v-else-if="model.directConnectionCount !== null">1 跳 · {{ model.directConnectionCount }} 条连接</span>
      <span v-if="model.goalCapability">目标 · {{ model.goalCapability }}</span>
    </p>

    <div class="research-actions" aria-label="选择节点研究方式">
      <button type="button" @click="emit('choose', 'understand')">
        <BookOpenCheck :size="15" /><span>梳理概念</span>
      </button>
      <button type="button" @click="emit('choose', 'evidence')">
        <FileSearch :size="15" /><span>补充证据</span>
      </button>
      <button type="button" @click="emit('choose', 'practice')">
        <Dumbbell :size="15" /><span>生成练习</span>
      </button>
    </div>
    <p class="research-guardrail">动作只填入可编辑任务；发送时才冻结节点与 revision。</p>
  </section>
</template>

<style scoped>
.node-research-panel { min-width:0; padding:10px 12px 9px; color:var(--sage-text); background:var(--sage-surface); }
.node-research-panel header { display:flex; align-items:center; gap:8px; min-width:0; }
.research-icon { display:grid; place-items:center; width:28px; height:28px; flex:none; border:1px solid color-mix(in srgb,var(--sage-source) 34%,var(--sage-border)); border-radius:var(--sage-radius); color:var(--sage-source); background:var(--sage-source-bg); }
.node-research-panel header>div { display:grid; min-width:0; gap:1px; }
.node-research-panel small { color:var(--sage-text-muted); font-size:10px; }
.node-research-panel strong { overflow:hidden; font-size:var(--sage-font-sm); text-overflow:ellipsis; white-space:nowrap; }
.node-research-panel em { flex:none; margin-left:auto; color:var(--sage-success); font-size:10px; font-style:normal; }
.node-research-panel em.limited { color:var(--sage-review-strong); }
.research-boundary { display:flex; align-items:center; gap:5px; min-width:0; margin:8px 0 7px; overflow:hidden; color:var(--sage-text-muted); font-size:10px; white-space:nowrap; }
.research-boundary>*+*::before { margin-right:5px; color:var(--sage-border-strong); content:'·'; }
.research-boundary code { overflow:hidden; color:var(--sage-text-secondary); font-family:var(--sage-font-mono); text-overflow:ellipsis; }
.research-actions { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }
.research-actions button { display:flex; align-items:center; justify-content:center; gap:5px; min-width:0; min-height:34px; padding:0 6px; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); color:var(--sage-text-secondary); background:var(--sage-surface-muted); font-size:11px; }
.research-actions button:hover { border-color:var(--sage-source); color:var(--sage-source); background:var(--sage-source-bg); }
.research-actions button:focus-visible { outline:2px solid var(--sage-focus); outline-offset:1px; }
.research-actions button span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.research-guardrail { margin:6px 0 0; color:var(--sage-text-muted); font-size:10px; line-height:1.4; }
@media (max-width:430px) { .research-boundary code:nth-of-type(n+2) { display:none; } }
</style>
