<script setup lang="ts">
import { CheckCircle2, CircleDot, Target } from 'lucide-vue-next'
import { computed } from 'vue'
import type { CodingThreadGoal } from '../../types/api'
import type { HarnessRunStatus } from '../../harness/types'

const props = defineProps<{
  sessionTitle: string
  threadGoal: CodingThreadGoal | null
  runStatus: HarnessRunStatus
}>()

const metCriteria = computed(() => props.threadGoal?.evaluation?.criteria.filter(
  (criterion) => criterion.status === 'met',
).length ?? 0)

const status = computed(() => {
  if (props.threadGoal?.status === 'satisfied') return { label: '目标完成', tone: 'completed', icon: CheckCircle2 }
  if (props.threadGoal?.status === 'blocked') return { label: '目标受阻', tone: 'blocked', icon: CircleDot }
  if (props.runStatus === 'running') return { label: '本轮运行中', tone: 'running', icon: CircleDot }
  if (props.runStatus === 'failed' || props.runStatus === 'cancelled') return { label: '本轮失败', tone: 'failed', icon: CircleDot }
  return { label: props.threadGoal ? '目标进行中' : '等待下一步', tone: 'idle', icon: CircleDot }
})
</script>

<template>
  <header class="goal-header" aria-label="当前目标">
    <div class="goal-leading">
      <span class="goal-mark"><Target :size="16" /></span>
      <span class="goal-copy">
        <small>{{ threadGoal ? '当前目标' : '当前会话' }}</small>
        <strong :title="threadGoal?.description || sessionTitle">{{ threadGoal?.description || sessionTitle }}</strong>
        <span v-if="threadGoal?.evaluation?.next_action">下一步：{{ threadGoal.evaluation.next_action }}</span>
        <span v-else>{{ threadGoal ? `${metCriteria} / ${threadGoal.completion_criteria.length} 条标准已有证据` : '尚未绑定长期目标，可以先直接对话' }}</span>
      </span>
    </div>
    <div class="goal-trailing">
      <span class="goal-state" :class="status.tone"><component :is="status.icon" :size="13" />{{ status.label }}</span>
      <span v-if="threadGoal" class="goal-criteria-count">{{ metCriteria }} / {{ threadGoal.completion_criteria.length }} 条标准已有证据</span>
      <div v-if="$slots.actions" class="goal-header-actions"><slot name="actions" /></div>
    </div>
  </header>
</template>

<style scoped>
.goal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  min-width: 0;
  min-height: 76px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--sage-border);
  background: var(--sage-surface);
}
.goal-leading { display: flex; align-items: center; gap: 11px; min-width: 0; }
.goal-mark { display: grid; place-items: center; width: 32px; height: 32px; flex: none; border: 1px solid var(--sage-border); border-radius: var(--sage-radius); color: var(--sage-brand); background: var(--sage-brand-bg); }
.goal-copy { display: grid; min-width: 0; gap: 2px; }
.goal-copy small { color: var(--sage-brand-strong); font-size: 10px; font-weight: 700; }
.goal-copy strong { max-width: 680px; overflow: hidden; color: var(--sage-text); font-size: var(--sage-font-md); text-overflow: ellipsis; white-space: nowrap; }
.goal-copy > span { max-width: 680px; overflow: hidden; color: var(--sage-text-muted); font-size: var(--sage-font-xs); text-overflow: ellipsis; white-space: nowrap; }
.goal-trailing { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex: none; }
.goal-state { display: inline-flex; align-items: center; gap: 5px; min-height: 26px; padding: 0 7px; border: 1px solid var(--sage-border); border-radius: var(--sage-radius-sm); color: var(--sage-text-muted); font-size: 10px; white-space: nowrap; }
.goal-state.running,.goal-state.completed { color: var(--sage-success); }
.goal-state.blocked { color: var(--sage-warning); }
.goal-state.failed { color: var(--sage-danger); }
.goal-criteria-count { color: var(--sage-text-muted); font-size: 10px; white-space: nowrap; }
.goal-header-actions { display: flex; align-items: center; gap: 4px; }
@media (max-width: 860px) { .goal-criteria-count { display: none; } }
@media (max-width: 600px) {
  .goal-header { min-height: 72px; padding: 8px 52px 8px 12px; gap: 8px; }
  .goal-leading { flex: 1; }
  .goal-mark { display: none; }
  .goal-copy strong { display: -webkit-box; max-width: none; overflow: hidden; white-space: normal; line-height: 1.28; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
  .goal-copy > span { display: none; }
  .goal-state { display: none; }
  .goal-trailing { gap: 4px; }
}
</style>
