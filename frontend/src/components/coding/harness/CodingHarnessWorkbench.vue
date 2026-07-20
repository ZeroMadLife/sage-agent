<script setup lang="ts">
import {
  Activity,
  AlertCircle,
  BarChart3,
  BookOpen,
  Braces,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock3,
  FlaskConical,
  Globe2,
  LoaderCircle,
  NotebookPen,
  PauseCircle,
  RotateCcw,
  Route,
  ShieldCheck,
  Target,
  Wrench,
  XCircle,
} from 'lucide-vue-next'
import { computed } from 'vue'
import type { CodingConnectionState } from '../../../stores/codingStream'
import type {
  CodingKnowledgeSourceProposal,
  CodingKnowledgeSourceProposalDetail,
} from '../../../types/api'
import type {
  HarnessOperationRef,
  HarnessProjection,
  HarnessStageStatus,
} from '../../../harness/types'
import type { HarnessReviewBundle as HarnessReviewBundleViewModel } from '../../../harness/reviewBundle'
import { projectLearningPath } from '../../../harness/learningPath'
import { HarnessReviewBundle } from '../../harness'

const props = withDefaults(defineProps<{
  projection: HarnessProjection
  sessionTitle: string
  toolCallCount?: number
  reviewBundle?: HarnessReviewBundleViewModel | null
  depositBusy?: boolean
  sourceProposals?: readonly CodingKnowledgeSourceProposal[]
  sourceDetails?: Readonly<Record<string, CodingKnowledgeSourceProposalDetail | undefined>>
  sourceBusy?: Readonly<Record<string, boolean | undefined>>
  sourceDetailBusy?: Readonly<Record<string, boolean | undefined>>
  sourceError?: string
  connectionState?: CodingConnectionState
}>(), {
  toolCallCount: 0,
  reviewBundle: null,
  depositBusy: false,
  sourceProposals: () => [],
  sourceDetails: () => ({}),
  sourceBusy: () => ({}),
  sourceDetailBusy: () => ({}),
  sourceError: '',
  connectionState: 'idle',
})

const emit = defineEmits<{
  openOperation: [operation: HarnessOperationRef]
  approveDeposit: [proposalId: string, revision: number]
  rejectDeposit: [proposalId: string, revision: number]
  approveSource: [proposalId: string, revision: number]
  rejectSource: [proposalId: string, revision: number]
  loadSourceDetail: [proposalId: string]
}>()

const statusLabel = computed(() => ({
  idle: '未开始',
  running: '运行中',
  blocked: '等待确认',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}[props.projection.status]))
type LearningJourney = 'contract' | 'active' | 'review' | 'recovery'
const journey = computed<LearningJourney>(() => {
  if (props.connectionState === 'recovering') return 'recovery'
  if (props.reviewBundle?.deposit.status === 'review' || props.sourceProposals.length) return 'review'
  if (!props.projection.runId && props.projection.status === 'idle') return 'contract'
  return 'active'
})
const journeyLabel = computed(() => ({
  contract: '目标定约',
  active: '主动学习',
  review: '沉淀审阅',
  recovery: '断线恢复',
}[journey.value]))
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
const runBudgetLabel = computed(() => (
  props.projection.runtimeResources?.find((resource) => resource.kind === 'budget')?.detail
  || props.projection.runtimeResources?.find((resource) => resource.kind === 'context')?.detail
  || '--'
))
const runBudgetCompactLabel = computed(() => runBudgetLabel.value.split(' · ')[0] || '--')
const visitedEvents = computed(() => (
  props.projection.stageEvents?.length
    ? props.projection.stageEvents
    : props.projection.stages
      .filter((stage) => stage.visitCount > 0)
      .slice()
      .sort((left, right) => left.lastSequence - right.lastSequence)
).slice(-8))

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
    :data-journey="journey"
    aria-label="Harness 路径画布"
  >
    <header class="workbench-header">
      <div class="workbench-title">
        <span class="workbench-mark" :class="projection.status"><Activity :size="15" /></span>
        <span class="workbench-title-copy">
          <strong>{{ journeyLabel }}</strong>
          <span :title="sessionTitle">{{ sessionTitle }}</span>
        </span>
      </div>
      <dl class="workbench-metrics">
        <div class="metric-state" :class="projection.status"><dt><Activity :size="13" />状态</dt><dd>{{ statusLabel }}</dd></div>
        <div><dt><Clock3 :size="13" />耗时</dt><dd>{{ elapsedLabel }}</dd></div>
        <div><dt><Braces :size="13" />迭代</dt><dd>{{ iterationCount }}</dd></div>
        <div><dt><Wrench :size="13" />工具</dt><dd>{{ toolCallCount }}</dd></div>
        <div class="metric-context"><dt>本轮</dt><dd :title="runBudgetLabel">{{ runBudgetCompactLabel }}</dd></div>
      </dl>
    </header>

    <div class="journey-canvas">
      <p v-if="sourceError && !sourceProposals.length" class="source-review-error" role="alert"><AlertCircle :size="14" />{{ sourceError }}</p>
      <section v-if="journey === 'contract'" class="journey-surface contract-surface">
        <header class="surface-heading">
          <p><Target :size="15" />Goal Contract</p>
          <h1>先把目标变成可验证的约定</h1>
          <span>当前只有会话主题，目标尚未确认，因此不会启动运行、计算进度或写入长期记忆。</span>
        </header>
        <ol class="contract-steps" aria-label="目标定约步骤">
          <li class="current"><span>1</span><strong>目标结果</strong></li>
          <li><span>2</span><strong>范围边界</strong></li>
          <li><span>3</span><strong>验证标准</strong></li>
          <li><span>4</span><strong>确认开始</strong></li>
        </ol>
        <section class="contract-fields" aria-label="Goal Contract 草稿">
          <div class="contract-outcome"><small>目标结果</small><strong>{{ sessionTitle }}</strong><span>来自当前 Thread 标题，等待在对话中确认</span></div>
          <div><small>知识范围</small><strong>尚未约定</strong><span>需要纳入和排除哪些主题</span></div>
          <div><small>实践方式</small><strong>按目标选择</strong><span>Coding 仅在需要时作为 Practice Engine</span></div>
          <div><small>验证标准</small><strong>尚未验证</strong><span>等待在对话中确认可观察证据</span></div>
        </section>
        <p class="surface-guidance"><ShieldCheck :size="15" />继续在右侧对话补充结果、边界和验证方式；确认前保持 <code>no run</code>。</p>
      </section>

      <section v-else-if="journey === 'review'" class="journey-surface review-surface">
        <header class="surface-heading review-heading">
          <p><NotebookPen :size="15" />Deposit Review</p>
          <h1>确认哪些内容进入长期系统</h1>
          <span>运行记录与实践结果已保留；Wiki、Knowledge Unit、Memory 与掌握度只有在批准后才形成长期变更。</span>
        </header>
        <div class="artifact-strip" aria-label="自动保存的本轮成果">
          <span><Route :size="15" /><strong>Run Trace</strong><small>sequence {{ projection.lastSequence || 0 }}</small></span>
          <span><BookOpen :size="15" /><strong>Evidence</strong><small>{{ reviewBundle?.evidence.items.length || 0 }} 条可追溯证据</small></span>
          <span><FlaskConical :size="15" /><strong>Practice</strong><small>{{ reviewBundle?.practice.completedToolCount || 0 }} 项工具完成</small></span>
        </div>
        <HarnessReviewBundle
          v-if="reviewBundle"
          :bundle="reviewBundle"
          :deposit-busy="depositBusy"
          :source-proposals="sourceProposals"
          :source-details="sourceDetails"
          :source-busy="sourceBusy"
          :source-detail-busy="sourceDetailBusy"
          :source-error="sourceError"
          @approve-deposit="emit('approveDeposit', $event, reviewBundle.deposit.revision)"
          @reject-deposit="emit('rejectDeposit', $event, reviewBundle.deposit.revision)"
          @approve-source="emit('approveSource', $event, sourceProposals.find((item) => item.proposal_id === $event)?.revision ?? 0)"
          @reject-source="emit('rejectSource', $event, sourceProposals.find((item) => item.proposal_id === $event)?.revision ?? 0)"
          @load-source-detail="emit('loadSourceDetail', $event)"
        />
      </section>

      <section v-else-if="journey === 'recovery'" class="journey-surface recovery-surface">
        <header class="surface-heading recovery-heading">
          <p><RotateCcw :size="15" />Connection Recovery</p>
          <h1>连接中断，已确认的运行事实仍被保留</h1>
          <span>Sage 只从最后确认的 timeline sequence 继续订阅，不重新执行，也不重新标记已完成工具。</span>
        </header>
        <section class="recovery-band"><RotateCcw :size="19" /><span><strong>正在恢复 Harness 连接</strong><small>最后确认 sequence {{ projection.lastSequence || 0 }}</small></span><code>recovering</code></section>
        <div class="recovery-grid">
          <section class="event-replay"><h2>最后确认的运行路径</h2><ul><li v-for="stage in visitedEvents" :key="'eventId' in stage ? stage.eventId : stage.id"><time>{{ eventTime('timestamp' in stage ? stage.timestamp : stage.completedAt || stage.startedAt) }}</time><component :is="iconForStatus(stage.status)" :size="13" /><span>{{ stage.detail || stage.label }}</span><code>{{ stageStatusLabel(stage.status) }}</code></li></ul></section>
          <section class="preserved-context"><h2>已保留</h2><ul><li><CheckCircle2 :size="14" />当前 Thread 与会话历史</li><li><CheckCircle2 :size="14" />已接收的 timeline 事件</li><li><CheckCircle2 :size="14" />提交时绑定的 Surface Context</li><li><CheckCircle2 :size="14" />尚未发送的本地输入</li></ul></section>
        </div>
      </section>

      <section v-else class="journey-surface active-surface">
        <header class="goal-heading">
          <div><p><Target :size="15" />Current Goal</p><h1>{{ sessionTitle }}</h1><span>当前 Thread 的学习路径；只有 timeline 与已验证证据会改变状态。</span></div>
          <div class="evidence-summary"><small>目标掌握度</small><strong>尚未验证</strong><span>等待 Mastery Evidence</span></div>
        </header>

        <section class="path-section">
          <header><strong>当前学习路径</strong><span>由真实 Harness 阶段投影</span></header>
          <ol class="learning-path" aria-label="学习增量路径">
            <li v-for="(step, index) in learningPath" :key="step.id" :class="step.status" :aria-current="step.sourceStageId === projection.activeStageId ? 'step' : undefined">
              <span class="path-node"><component :is="pathIcons[step.id]" :size="20" /></span><strong>{{ step.label }}</strong><small :title="step.caption">{{ step.caption }}</small><i v-if="index < learningPath.length - 1" aria-hidden="true"></i>
            </li>
          </ol>
        </section>

        <div class="active-grid">
          <section class="event-replay"><h2>本轮 Harness</h2><ul><li v-for="stage in visitedEvents" :key="'eventId' in stage ? stage.eventId : stage.id"><time>{{ eventTime('timestamp' in stage ? stage.timestamp : stage.completedAt || stage.startedAt) }}</time><component :is="iconForStatus(stage.status)" :size="13" /><span>{{ stage.detail || stage.label }}</span><code>{{ stageStatusLabel(stage.status) }}</code></li><li v-if="!visitedEvents.length" class="empty-event"><time>--</time><Circle :size="13" /><span>等待 timeline 事件</span><code>pending</code></li></ul></section>
          <section class="mastery-panel"><h2>能力证据</h2><div><span><strong>知识理解</strong><small>等待带 revision 的证据包</small></span><em>尚未验证</em></div><div><span><strong>实践恢复</strong><small>等待 Practice Engine 验证结果</small></span><em>尚未验证</em></div><div><span><strong>长期沉淀</strong><small>只统计已批准 Proposal</small></span><em>尚未验证</em></div></section>
        </div>

        <details class="run-details"><summary><span>运行资源</span><small>工具、MCP 与上下文事实</small><ChevronDown :size="15" /></summary><section class="runtime-resources"><div v-if="projection.runtimeResources?.length" class="resource-grid"><button v-for="resource in projection.runtimeResources" :key="resource.id" type="button" class="resource-card" :class="resource.status" :disabled="!resource.operationRef" :aria-label="resource.operationRef ? `查看${resource.label}运行详情` : undefined" @click="resource.operationRef && emit('openOperation', resource.operationRef)"><component :is="iconForStatus(resource.status)" :size="16" /><span><strong>{{ resource.label }}</strong><small :title="resource.detail">{{ resource.detail }}</small></span><ChevronRight v-if="resource.operationRef" class="resource-open" :size="14" /></button></div><p v-else>本轮尚无工具、MCP、上下文或子代理资源事件。</p></section></details>
      </section>
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
.journey-canvas { min-width:0; min-height:0; overflow:auto; scrollbar-gutter:stable; }
.source-review-error { display:flex; align-items:center; gap:7px; margin:0; padding:8px 14px; border-bottom:1px solid color-mix(in srgb,var(--sage-danger) 35%,var(--sage-border)); color:var(--sage-danger); background:var(--sage-danger-bg); font-size:10px; }
.journey-surface { min-height:100%; padding:clamp(22px,3vw,40px) clamp(20px,3.5vw,46px) 34px; }
.surface-heading p,.goal-heading p { display:flex; align-items:center; gap:7px; margin:0 0 7px; color:var(--sage-brand-strong); font-size:10px; font-weight:750; text-transform:uppercase; }.surface-heading h1,.goal-heading h1 { margin:0; color:var(--sage-text); font-size:clamp(21px,2.35cqw,28px); line-height:1.22; letter-spacing:0; }.surface-heading>span,.goal-heading>div>span { display:block; max-width:760px; margin-top:7px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); line-height:1.55; }
.goal-heading { display:grid; grid-template-columns:minmax(0,1fr) 150px; align-items:start; gap:24px; padding-bottom:20px; border-bottom:1px solid var(--sage-border); }.evidence-summary { display:grid; align-content:center; min-height:72px; padding-left:16px; border-left:1px solid var(--sage-border); }.evidence-summary small,.evidence-summary span { color:var(--sage-text-muted); font-size:9px; }.evidence-summary strong { margin:3px 0; color:var(--sage-text-secondary); font-size:16px; }.evidence-summary span { margin:0; }
.path-section { padding:16px 0 20px; border-bottom:1px solid var(--sage-border); }.path-section>header { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }.path-section>header strong { font-size:var(--sage-font-sm); }.path-section>header span { color:var(--sage-text-muted); font-size:10px; }
.learning-path { display:grid; grid-template-columns:repeat(6,minmax(72px,1fr)); margin:0; padding:0; list-style:none; }.learning-path li { position:relative; display:grid; justify-items:center; align-content:start; gap:6px; min-width:0; text-align:center; }.path-node { position:relative; z-index:2; display:grid; place-items:center; width:38px; height:38px; border:2px solid var(--sage-border-strong); border-radius:50%; color:var(--sage-text-muted); background:var(--sage-surface); }.learning-path li.completed .path-node { border-color:var(--sage-success); color:var(--sage-surface); background:var(--sage-success); }.learning-path li.running .path-node,.learning-path li[aria-current="step"] .path-node { border-color:var(--sage-success); color:var(--sage-success); box-shadow:0 0 0 4px color-mix(in srgb,var(--sage-success) 12%,transparent); }.learning-path li.blocked .path-node { border-color:var(--sage-warning); color:var(--sage-warning); }.learning-path li.failed .path-node,.learning-path li.cancelled .path-node { border-color:var(--sage-danger); color:var(--sage-danger); }.learning-path strong { width:100%; overflow:hidden; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }.learning-path small { width:100%; overflow:hidden; color:var(--sage-text-muted); font-size:9px; text-overflow:ellipsis; white-space:nowrap; }.learning-path li>i { position:absolute; z-index:1; top:18px; left:calc(50% + 19px); width:calc(100% - 38px); height:1px; background:var(--sage-border-strong); }.learning-path li.completed>i { background:var(--sage-success); }.learning-path li.running .path-node>svg { animation:path-spin 1.2s linear infinite; }
.active-grid,.recovery-grid { display:grid; grid-template-columns:minmax(0,1.15fr) minmax(240px,.85fr); gap:28px; padding:20px 0 16px; }.active-grid h2,.recovery-grid h2,.runtime-resources h2 { margin:0 0 10px; font-size:var(--sage-font-sm); }.event-replay ul,.preserved-context ul { margin:0; padding:0; list-style:none; }.event-replay li { display:grid; grid-template-columns:66px 15px minmax(0,1fr) auto; align-items:center; gap:8px; min-height:36px; border-top:1px solid var(--sage-border); color:var(--sage-text-muted); font-size:11px; }.event-replay time,.event-replay code { font-family:var(--sage-font-mono); font-size:9px; }.event-replay li>svg { color:var(--sage-success); }.event-replay span { overflow:hidden; color:var(--sage-text-secondary); text-overflow:ellipsis; white-space:nowrap; }
.mastery-panel>div { display:flex; align-items:center; gap:12px; min-height:54px; border-top:1px solid var(--sage-border); }.mastery-panel>div>span { display:grid; min-width:0; }.mastery-panel strong { font-size:11px; }.mastery-panel small { margin-top:2px; overflow:hidden; color:var(--sage-text-muted); font-size:9px; text-overflow:ellipsis; white-space:nowrap; }.mastery-panel em { margin-left:auto; color:var(--sage-text-muted); font-size:9px; font-style:normal; white-space:nowrap; }
.run-details { border-top:1px solid var(--sage-border); }.run-details>summary { display:grid; grid-template-columns:auto minmax(0,1fr) 18px; align-items:center; gap:8px; min-height:40px; color:var(--sage-text-secondary); cursor:pointer; list-style:none; font-size:var(--sage-font-xs); }.run-details>summary::-webkit-details-marker { display:none; }.run-details>summary small { color:var(--sage-text-muted); font-size:10px; }.run-details>summary svg { transition:transform .16s ease; }.run-details[open]>summary svg { transform:rotate(180deg); }.runtime-resources { padding:14px 0 4px; }.resource-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }.resource-card { display:grid; grid-template-columns:18px minmax(0,1fr) auto; align-items:center; gap:8px; min-height:48px; padding:7px 9px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text); background:var(--sage-surface); text-align:left; }.resource-card:not(:disabled) { cursor:pointer; }.resource-card:not(:disabled):hover { border-color:var(--sage-border-strong); background:var(--sage-surface-muted); }.resource-card:disabled { cursor:default; opacity:1; }.resource-card>svg:first-child { color:var(--sage-success); }.resource-open { color:var(--sage-text-muted); }.resource-grid span,.resource-grid strong,.resource-grid small { display:block; min-width:0; }.resource-grid strong { font-size:var(--sage-font-xs); }.resource-grid small { overflow:hidden; color:var(--sage-text-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }.runtime-resources>p { margin:0; color:var(--sage-text-muted); font-size:var(--sage-font-xs); line-height:1.6; }
.contract-steps { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); margin:24px 0 0; padding:0 0 18px; border-bottom:1px solid var(--sage-border); list-style:none; }.contract-steps li { display:flex; align-items:center; gap:8px; color:var(--sage-text-muted); font-size:10px; }.contract-steps span { display:grid; place-items:center; width:24px; height:24px; border:1px solid var(--sage-border-strong); border-radius:50%; }.contract-steps li.current { color:var(--sage-brand-strong); }.contract-steps li.current span { border-color:var(--sage-brand-strong); color:var(--sage-surface); background:var(--sage-brand-strong); }.contract-fields { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); border-bottom:1px solid var(--sage-border); }.contract-fields>div { min-height:104px; padding:18px 16px; border-right:1px solid var(--sage-border); }.contract-fields>div:last-child { border-right:0; }.contract-fields .contract-outcome { grid-column:1/-1; min-height:90px; border-right:0; border-bottom:1px solid var(--sage-border); background:var(--sage-success-bg); }.contract-fields small,.contract-fields span { display:block; color:var(--sage-text-muted); font-size:9px; }.contract-fields strong { display:block; margin:7px 0 4px; font-size:var(--sage-font-sm); line-height:1.45; }.surface-guidance { display:flex; align-items:center; gap:7px; margin:16px 0 0; color:var(--sage-text-secondary); font-size:var(--sage-font-xs); }.surface-guidance svg { color:var(--sage-brand-strong); }.surface-guidance code { color:var(--sage-text-muted); }
.review-heading p { color:var(--sage-review-strong); }.artifact-strip { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); margin-top:22px; border-top:1px solid var(--sage-border); border-bottom:1px solid var(--sage-border); }.artifact-strip>span { display:grid; grid-template-columns:22px minmax(0,1fr); align-content:center; min-height:60px; padding:9px 12px; border-right:1px solid var(--sage-border); }.artifact-strip>span:last-child { border-right:0; }.artifact-strip svg { grid-row:1/3; color:var(--sage-source); }.artifact-strip strong { font-size:10px; }.artifact-strip small { color:var(--sage-text-muted); font-size:9px; }.review-surface :deep(.review-bundle) { margin-top:18px; }
.recovery-heading p,.recovery-band>svg { color:var(--sage-source); }.recovery-band { display:grid; grid-template-columns:34px minmax(0,1fr) auto; align-items:center; gap:10px; min-height:66px; margin-top:22px; padding:10px 12px; border-left:3px solid var(--sage-source); background:var(--sage-source-bg); }.recovery-band span,.recovery-band strong,.recovery-band small { display:block; min-width:0; }.recovery-band strong { font-size:var(--sage-font-sm); }.recovery-band small,.recovery-band code { margin-top:2px; color:var(--sage-text-muted); font-size:9px; }.preserved-context li { display:flex; align-items:center; gap:8px; min-height:38px; border-top:1px solid var(--sage-border); color:var(--sage-text-secondary); font-size:10px; }.preserved-context svg { color:var(--sage-success); }
.canvas-status { display:flex; align-items:center; gap:18px; padding:0 14px; border-top:1px solid var(--sage-border); color:var(--sage-text-muted); font-size:10px; }.canvas-status span { display:flex; align-items:center; gap:6px; }.canvas-status span:last-child { margin-left:auto; }.canvas-status i { width:7px; height:7px; border-radius:50%; background:var(--sage-border-strong); }.canvas-status i.running,.canvas-status i.completed { background:var(--sage-success); }.canvas-status i.blocked { background:var(--sage-warning); }.canvas-status i.failed,.canvas-status i.cancelled { background:var(--sage-danger); }
@keyframes path-spin { to { transform:rotate(360deg); } }
@container coding-harness (max-width:900px) { .metric-context { display:none !important; }.active-grid,.recovery-grid { grid-template-columns:1fr; }.workbench-title-copy span { max-width:140px; }.contract-fields { grid-template-columns:1fr; }.contract-fields>div { border-right:0; border-bottom:1px solid var(--sage-border); }.contract-fields>div:last-child { border-bottom:0; } }
@media (max-width:767px) { .coding-harness-workbench { grid-template-rows:auto minmax(0,1fr); }.workbench-header { display:none; }.journey-surface { padding:66px 14px 74px; }.surface-heading h1,.goal-heading h1 { font-size:22px; }.goal-heading { grid-template-columns:1fr; gap:14px; }.evidence-summary { min-height:0; padding:10px 0 0; border-top:1px solid var(--sage-border); border-left:0; }.path-section>header span { display:none; }.learning-path { grid-template-columns:repeat(3,minmax(0,1fr)); gap:22px 4px; }.learning-path li>i { display:none; }.active-grid,.recovery-grid { gap:20px; }.runtime-resources { display:none; }.canvas-status { display:none; }.contract-steps { grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }.contract-fields { grid-template-columns:1fr; }.contract-fields>div { min-height:86px; border-right:0; border-bottom:1px solid var(--sage-border); }.artifact-strip { grid-template-columns:1fr; }.artifact-strip>span { border-right:0; border-bottom:1px solid var(--sage-border); }.artifact-strip>span:last-child { border-bottom:0; }.surface-guidance { align-items:flex-start; } }
@media (prefers-reduced-motion:reduce) { .learning-path li.running .path-node>svg { animation:none; }.run-details>summary svg { transition:none; } }
</style>
