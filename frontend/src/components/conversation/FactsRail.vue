<script setup lang="ts">
import {
  AlertTriangle,
  BookOpenCheck,
  CheckCircle2,
  ChevronRight,
  Circle,
  FileSearch,
  ListTree,
  LoaderCircle,
  PanelRightClose,
  RotateCcw,
  Target,
  Wrench,
  XCircle,
} from 'lucide-vue-next'
import { computed } from 'vue'
import type { CodingConnectionState } from '../../stores/codingStream'
import type { CodingThreadGoal } from '../../types/api'
import type { HarnessProjection, HarnessSurfaceContext } from '../../harness/types'
import type { HarnessReviewBundle } from '../../harness/reviewBundle'
import { harnessRunVisualState } from '../../harness/runVisualState'
import ContextReceiptChip from './ContextReceiptChip.vue'

const props = withDefaults(defineProps<{
  projection: HarnessProjection
  connectionState: CodingConnectionState
  threadGoal: CodingThreadGoal | null
  reviewBundle: HarnessReviewBundle
  sourceProposalCount?: number
  context?: HarnessSurfaceContext | null
  toolCallCount?: number
  goalBusy?: boolean
  showHeader?: boolean
}>(), {
  sourceProposalCount: 0,
  context: null,
  toolCallCount: 0,
  goalBusy: false,
  showHeader: true,
})

const emit = defineEmits<{
  close: []
  openDetails: []
  evaluateGoal: []
  continueGoal: []
  removeContext: []
}>()

const visualState = computed(() => harnessRunVisualState(props.projection, props.connectionState))
const stateMeta = computed(() => ({
  idle: { label: '未开始', icon: Circle },
  running: { label: '运行中', icon: LoaderCircle },
  tool: { label: '调用工具', icon: Wrench },
  approval: { label: '等待审批', icon: AlertTriangle },
  failed: { label: '运行失败', icon: XCircle },
  completed: { label: '已完成', icon: CheckCircle2 },
  recovering: { label: '恢复连接', icon: RotateCcw },
}[visualState.value]))
const failureOrRecovery = computed(() => visualState.value === 'failed' || visualState.value === 'recovering')
const currentStage = computed(() => (
  props.projection.stages.find((stage) => stage.id === props.projection.activeStageId)
  || [...props.projection.stages].reverse().find((stage) => stage.visitCount > 0)
))
const pendingProposalCount = computed(() => (
  (props.reviewBundle.deposit.status === 'review' ? 1 : 0) + props.sourceProposalCount
))
const hasEvidence = computed(() => (
  props.reviewBundle.evidence.items.length > 0
  || props.reviewBundle.practice.completedToolCount > 0
  || pendingProposalCount.value > 0
))
const hasFacts = computed(() => Boolean(
  props.threadGoal
  || props.projection.runId
  || hasEvidence.value
  || props.context
  || failureOrRecovery.value,
))
const blockerLabel = computed(() => ({
  missing_evidence: '缺少可验证证据',
  needs_user_input: '等待用户输入',
  run_failed: '上一轮运行失败',
  external_wait: '等待外部系统',
  goal_not_met_yet: '目标尚未满足',
  no_progress: '本轮没有新增进展',
}[props.threadGoal?.evaluation?.blocker || 'goal_not_met_yet']))
</script>

<template>
  <section class="facts-rail" :class="{ 'without-header': !showHeader }" aria-label="本轮事实" :data-run-id="projection.runId" :data-run-state="visualState">
    <header v-if="showHeader" class="facts-header">
      <span><small>Facts</small><strong>本轮事实</strong></span>
      <button type="button" aria-label="收起事实栏" title="收起事实栏" @click="emit('close')"><PanelRightClose :size="16" /></button>
    </header>

    <div class="facts-content">
      <section v-if="$slots.attention" class="fact-section fact-attention" data-fact="attention">
        <header><AlertTriangle :size="14" /><strong>需要你决定</strong></header>
        <slot name="attention" />
      </section>

      <section v-if="failureOrRecovery" class="fact-section fact-recovery" data-fact="recovery">
        <header><component :is="stateMeta.icon" :size="14" /><strong>{{ stateMeta.label }}</strong></header>
        <p>{{ visualState === 'recovering' ? `从 sequence ${projection.lastSequence || 0} 继续订阅，不重新执行。` : currentStage?.detail || '本轮运行未完成，已保留现有 timeline。' }}</p>
        <button type="button" @click="emit('openDetails')">查看稳定点<ChevronRight :size="13" /></button>
      </section>

      <section v-if="threadGoal" class="fact-section fact-goal" data-fact="goal">
        <header><Target :size="14" /><strong>当前目标</strong><code>rev {{ threadGoal.revision }}</code></header>
        <p class="fact-primary">{{ threadGoal.description }}</p>
        <p v-if="threadGoal.evaluation" class="fact-secondary">{{ blockerLabel }} · {{ threadGoal.evaluation.next_action }}</p>
        <ul><li v-for="(criterion, index) in threadGoal.completion_criteria.slice(0, 3)" :key="criterion"><span>{{ threadGoal.evaluation?.criteria[index]?.status === 'met' ? '✓' : '○' }}</span>{{ criterion }}</li></ul>
        <div class="fact-actions">
          <button type="button" :disabled="goalBusy || projection.status === 'running'" @click="emit('evaluateGoal')">评估本轮</button>
          <button type="button" class="primary" :disabled="goalBusy || connectionState !== 'connected' || projection.status === 'running'" @click="emit('continueGoal')">继续目标</button>
        </div>
      </section>

      <section v-if="projection.runId" class="fact-section fact-run" data-fact="run">
        <header><ListTree :size="14" /><strong>本轮 Run</strong><code>{{ projection.runId.slice(0, 12) }}</code></header>
        <div class="run-summary"><component :is="stateMeta.icon" :size="15" /><span><strong>{{ stateMeta.label }}</strong><small>{{ currentStage?.label || '等待下一阶段' }}</small></span><em>{{ toolCallCount }} 工具</em></div>
        <div class="run-stage-track" :aria-label="`${projection.stages.filter((stage) => stage.status === 'completed').length}/${projection.stages.length} 个阶段完成`"><i v-for="stage in projection.stages" :key="stage.id" :class="stage.status" :title="`${stage.label} · ${stage.status}`"></i></div>
      </section>

      <section v-if="hasEvidence" class="fact-section fact-evidence" data-fact="evidence">
        <header><FileSearch :size="14" /><strong>证据与沉淀</strong></header>
        <div v-if="reviewBundle.evidence.items.length || reviewBundle.practice.completedToolCount" class="fact-metrics">
          <span v-if="reviewBundle.evidence.items.length"><BookOpenCheck :size="14" /><strong>{{ reviewBundle.evidence.items.length }} 条</strong><small>证据</small></span>
          <span v-if="reviewBundle.practice.completedToolCount"><Wrench :size="14" /><strong>{{ reviewBundle.practice.completedToolCount }} 项</strong><small>实践</small></span>
        </div>
        <button v-if="pendingProposalCount" class="proposal-action" type="button" @click="emit('openDetails')"><AlertTriangle :size="13" />{{ pendingProposalCount }} 条待审阅沉淀<ChevronRight :size="13" /></button>
      </section>

      <section v-if="context" class="fact-section fact-context" data-fact="context">
        <header><BookOpenCheck :size="14" /><strong>已选上下文</strong></header>
        <ContextReceiptChip :context="context" removable @remove="emit('removeContext')" />
      </section>

      <div v-if="!hasFacts && !$slots.attention" class="facts-empty"><Circle :size="17" /><strong>等待你的下一步</strong><span>发送消息后，这里只显示影响下一步决策的事实。</span></div>
    </div>

    <footer class="facts-footer"><button type="button" @click="emit('openDetails')">打开完整运行详情<ChevronRight :size="14" /></button></footer>
  </section>
</template>

<style scoped>
.facts-rail { display: grid; grid-template-rows: 52px minmax(0, 1fr) 42px; width: 100%; height: 100%; min-width: 0; min-height: 0; color: var(--sage-text); background: var(--sage-surface); }
.facts-rail.without-header { grid-template-rows: minmax(0, 1fr) 42px; }
.facts-header { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 0 10px 0 14px; border-bottom: 1px solid var(--sage-border); }
.facts-header > span { display: grid; gap: 1px; }.facts-header small { color: var(--sage-brand-strong); font-size: 9px; font-weight: 700; text-transform: uppercase; }.facts-header strong { font-size: var(--sage-font-sm); }
.facts-header button { display: grid; place-items: center; width: 30px; height: 30px; padding: 0; border: 0; border-radius: var(--sage-radius-sm); color: var(--sage-text-muted); background: transparent; }.facts-header button:hover { color: var(--sage-text); background: var(--sage-surface-muted); }
.facts-content { min-height: 0; overflow-y: auto; scrollbar-gutter: stable; }
.fact-section { padding: 13px 14px; border-bottom: 1px solid var(--sage-border); }
.fact-section > header { display: flex; align-items: center; gap: 6px; min-width: 0; margin-bottom: 9px; color: var(--sage-text-secondary); }.fact-section > header > svg { flex: none; }.fact-section > header strong { font-size: var(--sage-font-xs); }.fact-section > header code { margin-left: auto; color: var(--sage-text-muted); font-size: 9px; }
.fact-section p { margin: 0; color: var(--sage-text-muted); font-size: 11px; line-height: 1.55; }.fact-primary { color: var(--sage-text-secondary) !important; font-weight: 600; }.fact-secondary { margin-top: 5px !important; }
.fact-attention { border-left: 3px solid var(--sage-warning); background: var(--sage-warning-bg); }.fact-attention > header { color: var(--sage-warning); }.fact-attention :deep(.approval-card) { margin: 0; border: 0; background: transparent; }
.fact-recovery { border-left: 3px solid var(--sage-danger); background: var(--sage-danger-bg); }.fact-recovery > header { color: var(--sage-danger); }.facts-rail[data-run-state="recovering"] .fact-recovery { border-left-color: var(--sage-source); background: var(--sage-source-bg); }.facts-rail[data-run-state="recovering"] .fact-recovery > header { color: var(--sage-source); }
.fact-recovery > button,.proposal-action { display: flex; align-items: center; gap: 5px; min-height: 30px; margin-top: 9px; padding: 0; border: 0; color: var(--sage-source); background: transparent; font-size: 10px; }
.fact-goal ul { display: grid; gap: 5px; margin: 10px 0 0; padding: 0; list-style: none; }.fact-goal li { display: grid; grid-template-columns: 14px minmax(0, 1fr); gap: 5px; color: var(--sage-text-muted); font-size: 10px; line-height: 1.4; }.fact-goal li span { color: var(--sage-success); }
.fact-actions { display: flex; gap: 6px; margin-top: 11px; }.fact-actions button { min-height: 30px; padding: 0 8px; border: 1px solid var(--sage-border); border-radius: var(--sage-radius-sm); color: var(--sage-text-secondary); background: var(--sage-surface); font-size: 10px; }.fact-actions button.primary { border-color: var(--sage-brand-strong); color: white; background: var(--sage-brand-strong); }.fact-actions button:disabled { cursor: not-allowed; opacity: .5; }
.run-summary { display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; align-items: center; gap: 7px; }.run-summary > span { display: grid; }.run-summary strong { font-size: 11px; }.run-summary small,.run-summary em { color: var(--sage-text-muted); font-size: 9px; font-style: normal; }
.run-stage-track { display: grid; grid-template-columns: repeat(auto-fit, minmax(12px, 1fr)); gap: 3px; height: 3px; margin-top: 9px; }.run-stage-track i { border-radius: 2px; background: var(--sage-border); }.run-stage-track i.completed { background: var(--sage-success); }.run-stage-track i.running { background: var(--sage-source); }.run-stage-track i.blocked { background: var(--sage-warning); }.run-stage-track i.failed,.run-stage-track i.cancelled { background: var(--sage-danger); }
.fact-metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border: 1px solid var(--sage-border); border-radius: var(--sage-radius); }.fact-metrics > span { display: grid; grid-template-columns: 18px minmax(0, 1fr); align-items: center; min-height: 48px; padding: 7px 8px; border-right: 1px solid var(--sage-border); }.fact-metrics > span:last-child { border-right: 0; }.fact-metrics svg { grid-row: 1 / 3; color: var(--sage-source); }.fact-metrics strong { font-size: 13px; }.fact-metrics small { color: var(--sage-text-muted); font-size: 9px; }
.proposal-action { width: 100%; justify-content: flex-start; color: var(--sage-review-strong); }.proposal-action svg:last-child { margin-left: auto; }
.fact-context :deep(.context-receipt-chip) { grid-template-columns: 18px minmax(0, 1fr) auto; }.fact-context :deep(.context-receipt-state) { grid-column: 2; }.fact-context :deep(.context-receipt-chip button) { grid-column: 3; grid-row: 1 / 3; }
.facts-empty { display: flex; min-height: 180px; flex-direction: column; align-items: center; justify-content: center; gap: 6px; padding: 24px; color: var(--sage-text-muted); text-align: center; }.facts-empty strong { color: var(--sage-text-secondary); font-size: var(--sage-font-sm); }.facts-empty span { max-width: 230px; font-size: 10px; line-height: 1.55; }
.facts-footer { display: flex; align-items: center; padding: 0 9px; border-top: 1px solid var(--sage-border); }.facts-footer button { display: flex; align-items: center; justify-content: center; gap: 5px; width: 100%; min-height: 30px; padding: 0; border: 0; border-radius: var(--sage-radius-sm); color: var(--sage-text-muted); background: transparent; font-size: 10px; }.facts-footer button:hover { color: var(--sage-text); background: var(--sage-surface-muted); }
</style>
