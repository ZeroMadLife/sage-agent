<script setup lang="ts">
import {
  Activity,
  BarChart3,
  BookOpen,
  Braces,
  CheckCircle2,
  ChevronDown,
  Circle,
  Clock3,
  FlaskConical,
  Globe2,
  LoaderCircle,
  NotebookPen,
  PauseCircle,
  Route,
  Target,
  Wrench,
  XCircle,
} from 'lucide-vue-next'
import { computed } from 'vue'
import type { HarnessProjection, HarnessStageStatus } from '../../../harness/types'
import type { HarnessReviewBundle as HarnessReviewBundleViewModel } from '../../../harness/reviewBundle'
import { projectLearningPath } from '../../../harness/learningPath'
import { HarnessReviewBundle } from '../../harness'

const props = withDefaults(defineProps<{
  projection: HarnessProjection
  sessionTitle: string
  toolCallCount?: number
  reviewBundle?: HarnessReviewBundleViewModel | null
  depositBusy?: boolean
}>(), {
  toolCallCount: 0,
  reviewBundle: null,
  depositBusy: false,
})

const emit = defineEmits<{
  approveDeposit: [proposalId: string, revision: number]
  rejectDeposit: [proposalId: string, revision: number]
}>()

const statusLabel = computed(() => ({
  idle: '等待任务',
  running: 'LIVE',
  blocked: '等待确认',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}[props.projection.status]))
const learningPath = computed(() => projectLearningPath(props.projection))
const pathIcons = {
  goal: Target,
  knowledge: BookOpen,
  evidence: Globe2,
  practice: FlaskConical,
  mastery: BarChart3,
  deposit: NotebookPen,
}
const iterationCount = computed(() => (
  props.projection.stages.find((stage) => stage.id === 'plan')?.visitCount ?? 0
))
const elapsedLabel = computed(() => {
  const elapsed = props.projection.stages.reduce((total, stage) => total + (stage.durationMs ?? 0), 0)
  if (elapsed < 1_000) return elapsed ? `${elapsed}ms` : '--'
  if (elapsed < 60_000) return `${(elapsed / 1_000).toFixed(elapsed < 10_000 ? 1 : 0)}s`
  return `${Math.floor(elapsed / 60_000)}m ${Math.round(elapsed % 60_000 / 1_000)}s`
})
const contextLabel = computed(() => (
  props.projection.runtimeResources?.find((resource) => resource.kind === 'context')?.detail || '--'
))
const visitedEvents = computed(() => props.projection.stages
  .filter((stage) => stage.visitCount > 0)
  .slice()
  .sort((left, right) => left.lastSequence - right.lastSequence)
  .slice(-5))

function iconForStatus(status: HarnessStageStatus) {
  if (status === 'running') return LoaderCircle
  if (status === 'blocked') return PauseCircle
  if (status === 'completed') return CheckCircle2
  if (status === 'failed' || status === 'cancelled') return XCircle
  return Circle
}

function stageStatusLabel(status: HarnessStageStatus) {
  if (status === 'running') return 'stage_started'
  if (status === 'blocked') return 'approval_required'
  if (status === 'completed') return 'stage_completed'
  if (status === 'failed') return 'stage_failed'
  if (status === 'cancelled') return 'cancelled'
  return 'pending'
}

function eventTime(value?: string) {
  if (!value) return '--'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '--'
    : new Intl.DateTimeFormat('zh-CN', {
        hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
      }).format(date)
}
</script>

<template>
  <section
    class="coding-harness-workbench"
    :data-run-id="projection.runId"
    :data-status="projection.status"
    aria-label="Harness 路径画布"
  >
    <header class="workbench-header">
      <div class="workbench-title">
        <span class="workbench-mark" :class="projection.status"><Activity :size="15" /></span>
        <span class="workbench-title-copy">
          <strong>Harness 2.0</strong>
          <span :title="sessionTitle">{{ sessionTitle }}</span>
        </span>
      </div>
      <dl class="workbench-metrics">
        <div class="metric-state" :class="projection.status"><dt><Activity :size="13" />状态</dt><dd>{{ statusLabel }}</dd></div>
        <div><dt><Clock3 :size="13" />耗时</dt><dd>{{ elapsedLabel }}</dd></div>
        <div><dt><Braces :size="13" />迭代</dt><dd>{{ iterationCount }}</dd></div>
        <div><dt><Wrench :size="13" />工具</dt><dd>{{ toolCallCount }}</dd></div>
        <div class="metric-context"><dt>上下文</dt><dd :title="contextLabel">{{ contextLabel }}</dd></div>
      </dl>
    </header>

    <div class="journey-canvas">
      <section class="journey-intro">
        <p><Route :size="15" />Harness 状态画布 · 来自 timeline</p>
        <h1>把一次回答变成可验证的学习增量</h1>
        <span>中心画布只呈现已经发生的检索、工具、审批与沉淀事件；Chat Dock 保留对话和操作入口。</span>
      </section>

      <section class="goal-band">
        <Target :size="19" />
        <span><strong>本轮目标：{{ sessionTitle }}</strong><small>{{ projection.runId ? `run ${projection.runId}` : '等待开始' }}</small></span>
        <em :class="projection.status">{{ statusLabel }}</em>
      </section>

      <ol class="learning-path" aria-label="学习增量路径">
        <li
          v-for="(step, index) in learningPath"
          :key="step.id"
          :class="step.status"
          :aria-current="step.sourceStageId === projection.activeStageId ? 'step' : undefined"
        >
          <span class="path-node"><component :is="pathIcons[step.id]" :size="20" /></span>
          <strong>{{ step.label }}</strong>
          <small :title="step.caption">{{ step.caption }}</small>
          <i v-if="index < learningPath.length - 1" aria-hidden="true"></i>
        </li>
      </ol>

      <HarnessReviewBundle
        v-if="reviewBundle"
        :bundle="reviewBundle"
        :deposit-busy="depositBusy"
        @approve-deposit="emit('approveDeposit', $event, reviewBundle.deposit.revision)"
        @reject-deposit="emit('rejectDeposit', $event, reviewBundle.deposit.revision)"
      />

      <details class="run-details">
        <summary><span>运行明细</span><small>事件回放与资源</small><ChevronDown :size="15" /></summary>
        <div class="journey-lower">
        <section class="event-replay">
          <h2>事件回放</h2>
          <ul>
            <li v-for="stage in visitedEvents" :key="stage.id">
              <time>{{ eventTime(stage.completedAt || stage.startedAt) }}</time>
              <component :is="iconForStatus(stage.status)" :size="13" />
              <span>{{ stage.detail || stage.label }}</span>
              <code>{{ stageStatusLabel(stage.status) }}</code>
            </li>
            <li v-if="!visitedEvents.length" class="empty-event">
              <time>--</time><Circle :size="13" /><span>等待 timeline 事件</span><code>pending</code>
            </li>
          </ul>
        </section>

        <section class="runtime-resources">
          <h2>本轮资源</h2>
          <div v-if="projection.runtimeResources?.length" class="resource-grid">
            <article v-for="resource in projection.runtimeResources" :key="resource.id" :class="resource.status">
              <component :is="iconForStatus(resource.status)" :size="16" />
              <span><strong>{{ resource.label }}</strong><small :title="resource.detail">{{ resource.detail }}</small></span>
            </article>
          </div>
          <p v-else>本轮尚无工具、MCP、上下文或子代理资源事件。</p>
        </section>
        </div>
      </details>
    </div>

    <footer class="canvas-status">
      <span><i :class="projection.status"></i>{{ projection.lastSequence ? `Timeline 已同步 · sequence ${projection.lastSequence}` : '等待 Timeline' }}</span>
      <span>{{ learningPath.length }} 个阶段</span>
    </footer>
  </section>
</template>

<style scoped>
.coding-harness-workbench { container:coding-harness / inline-size; display:grid; grid-template-rows:54px minmax(0,1fr) 32px; width:100%; height:100%; min-width:0; min-height:0; color:var(--sage-text); background:var(--sage-surface); }
.workbench-header { display:flex; align-items:center; gap:14px; min-width:0; padding:0 16px 0 18px; border-bottom:1px solid var(--sage-border); }
.workbench-title { display:flex; align-items:center; gap:9px; min-width:0; }.workbench-mark { display:grid; place-items:center; flex:none; width:30px; height:30px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-muted); background:var(--sage-surface-raised); }.workbench-mark.running,.workbench-mark.completed { color:var(--sage-success); }.workbench-mark.blocked { color:var(--sage-warning); }.workbench-mark.failed,.workbench-mark.cancelled { color:var(--sage-danger); }.workbench-title-copy { display:grid; min-width:0; }.workbench-title-copy strong { font-size:var(--sage-font-md); }.workbench-title-copy span { max-width:220px; overflow:hidden; color:var(--sage-text-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.workbench-metrics { display:flex; align-self:stretch; min-width:0; margin:0 0 0 auto; }.workbench-metrics>div { display:grid; align-content:center; min-width:66px; padding:0 9px; border-left:1px solid var(--sage-border); }.workbench-metrics dt,.workbench-metrics dd { margin:0; }.workbench-metrics dt { display:flex; align-items:center; gap:4px; color:var(--sage-text-muted); font-size:9px; }.workbench-metrics dd { margin-top:2px; color:var(--sage-text-secondary); font-family:var(--sage-font-mono); font-size:var(--sage-font-xs); }.metric-state.running dd,.metric-state.completed dd { color:var(--sage-success); }.metric-state.blocked dd { color:var(--sage-warning); }.metric-state.failed dd,.metric-state.cancelled dd { color:var(--sage-danger); }.workbench-metrics .metric-context { min-width:130px; max-width:190px; }.metric-context dd { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.journey-canvas { min-width:0; min-height:0; padding:clamp(26px,4vw,52px) clamp(22px,4vw,54px) 34px; overflow:auto; scrollbar-gutter:stable; }
.journey-intro p { display:flex; align-items:center; gap:7px; margin:0 0 10px; color:var(--sage-brand-strong); font-size:var(--sage-font-xs); font-weight:750; text-transform:uppercase; }.journey-intro h1 { margin:0; color:var(--sage-text); font-size:clamp(24px,3.1cqw,36px); line-height:1.2; letter-spacing:0; }.journey-intro>span { display:block; max-width:760px; margin-top:8px; color:var(--sage-text-muted); font-size:var(--sage-font-sm); line-height:1.6; }
.goal-band { display:grid; grid-template-columns:34px minmax(0,1fr) auto; align-items:center; gap:10px; min-height:58px; margin-top:26px; padding:9px 12px; border-left:3px solid var(--sage-success); background:var(--sage-success-bg); }.goal-band>svg { color:var(--sage-success); }.goal-band span,.goal-band strong,.goal-band small { display:block; min-width:0; }.goal-band strong { overflow:hidden; font-size:var(--sage-font-md); text-overflow:ellipsis; white-space:nowrap; }.goal-band small { margin-top:2px; color:var(--sage-text-muted); font-family:var(--sage-font-mono); font-size:10px; }.goal-band em { color:var(--sage-text-muted); font-size:var(--sage-font-xs); font-style:normal; }.goal-band em.running,.goal-band em.completed { color:var(--sage-success); }.goal-band em.blocked { color:var(--sage-warning); }.goal-band em.failed,.goal-band em.cancelled { color:var(--sage-danger); }
.learning-path { display:grid; grid-template-columns:repeat(6,minmax(88px,1fr)); margin:34px 0 0; padding:0 0 30px; border-bottom:1px solid var(--sage-border); list-style:none; }.learning-path li { position:relative; display:grid; justify-items:center; align-content:start; gap:7px; min-width:0; text-align:center; }.path-node { position:relative; z-index:2; display:grid; place-items:center; width:44px; height:44px; border:2px solid var(--sage-border-strong); border-radius:50%; color:var(--sage-text-muted); background:var(--sage-surface); }.learning-path li.completed .path-node { border-color:var(--sage-success); color:var(--sage-surface); background:var(--sage-success); }.learning-path li.running .path-node,.learning-path li[aria-current="step"] .path-node { border-color:var(--sage-success); color:var(--sage-success); box-shadow:0 0 0 5px color-mix(in srgb,var(--sage-success) 12%,transparent); }.learning-path li.blocked .path-node { border-color:var(--sage-warning); color:var(--sage-warning); }.learning-path li.failed .path-node,.learning-path li.cancelled .path-node { border-color:var(--sage-danger); color:var(--sage-danger); }.learning-path strong { width:100%; overflow:hidden; font-size:var(--sage-font-sm); text-overflow:ellipsis; white-space:nowrap; }.learning-path small { width:100%; overflow:hidden; color:var(--sage-text-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }.learning-path li>i { position:absolute; z-index:1; top:21px; left:calc(50% + 22px); width:calc(100% - 44px); height:1px; background:var(--sage-border-strong); }.learning-path li.completed>i { background:var(--sage-success); }.learning-path li.running .path-node>svg { animation:path-spin 1.2s linear infinite; }
.run-details { margin-top:14px; border-top:1px solid var(--sage-border); }.run-details>summary { display:grid; grid-template-columns:auto minmax(0,1fr) 18px; align-items:center; gap:8px; min-height:40px; color:var(--sage-text-secondary); cursor:pointer; list-style:none; font-size:var(--sage-font-xs); }.run-details>summary::-webkit-details-marker { display:none; }.run-details>summary small { color:var(--sage-text-muted); font-size:10px; }.run-details>summary svg { transition:transform .16s ease; }.run-details[open]>summary svg { transform:rotate(180deg); }
.journey-lower { display:grid; grid-template-columns:minmax(0,1.15fr) minmax(260px,.85fr); gap:34px; margin-top:24px; }.journey-lower h2 { margin:0 0 12px; font-size:var(--sage-font-sm); }.event-replay ul { margin:0; padding:0; list-style:none; }.event-replay li { display:grid; grid-template-columns:66px 15px minmax(0,1fr) auto; align-items:center; gap:8px; min-height:36px; border-top:1px solid var(--sage-border); color:var(--sage-text-muted); font-size:11px; }.event-replay time,.event-replay code { font-family:var(--sage-font-mono); font-size:10px; }.event-replay li>svg { color:var(--sage-success); }.event-replay span { overflow:hidden; color:var(--sage-text-secondary); text-overflow:ellipsis; white-space:nowrap; }.resource-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }.resource-grid article { display:grid; grid-template-columns:18px minmax(0,1fr); align-items:center; gap:8px; min-height:48px; padding:7px 9px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); }.resource-grid article>svg { color:var(--sage-success); }.resource-grid span,.resource-grid strong,.resource-grid small { display:block; min-width:0; }.resource-grid strong { font-size:var(--sage-font-xs); }.resource-grid small { overflow:hidden; color:var(--sage-text-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }.runtime-resources>p { margin:0; color:var(--sage-text-muted); font-size:var(--sage-font-xs); line-height:1.6; }
.canvas-status { display:flex; align-items:center; gap:18px; padding:0 14px; border-top:1px solid var(--sage-border); color:var(--sage-text-muted); font-size:10px; }.canvas-status span { display:flex; align-items:center; gap:6px; }.canvas-status span:last-child { margin-left:auto; }.canvas-status i { width:7px; height:7px; border-radius:50%; background:var(--sage-border-strong); }.canvas-status i.running,.canvas-status i.completed { background:var(--sage-success); }.canvas-status i.blocked { background:var(--sage-warning); }.canvas-status i.failed,.canvas-status i.cancelled { background:var(--sage-danger); }
@keyframes path-spin { to { transform:rotate(360deg); } }
@container coding-harness (max-width:900px) { .metric-context { display:none !important; }.journey-lower { grid-template-columns:1fr; }.workbench-title-copy span { max-width:140px; } }
@media (max-width:767px) { .coding-harness-workbench { grid-template-rows:auto minmax(0,1fr); }.workbench-header { display:none; }.journey-canvas { padding:68px 15px 74px; }.journey-intro h1 { font-size:26px; }.journey-intro>span { font-size:var(--sage-font-xs); }.goal-band { margin-top:18px; }.goal-band em { display:none; }.learning-path { grid-template-columns:repeat(3,minmax(0,1fr)); gap:24px 4px; margin-top:26px; padding-bottom:24px; }.learning-path li>i { display:none; }.journey-lower { gap:24px; }.runtime-resources { display:none; }.canvas-status { display:none; } }
@media (prefers-reduced-motion:reduce) { .learning-path li.running .path-node>svg { animation:none; }.run-details>summary svg { transition:none; } }
</style>
