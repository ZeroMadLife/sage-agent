<script setup lang="ts">
import {
  Bot,
  CheckCircle2,
  Circle,
  Clock3,
  Gauge,
  Database,
  LoaderCircle,
  PauseCircle,
  PlugZap,
  RotateCcw,
  XCircle,
} from 'lucide-vue-next'
import { computed } from 'vue'
import type {
  HarnessProjection,
  HarnessRuntimeResource,
  HarnessStageStatus,
} from '../../harness/types'

const props = withDefaults(defineProps<{
  projection: HarnessProjection
  showHeader?: boolean
}>(), {
  showHeader: true,
})

const statusLabel = computed(() => {
  const labels = {
    idle: '等待任务',
    running: '运行中',
    blocked: '等待确认',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return labels[props.projection.status]
})

function iconFor(status: HarnessStageStatus) {
  if (status === 'running') return LoaderCircle
  if (status === 'blocked') return PauseCircle
  if (status === 'completed') return CheckCircle2
  if (status === 'failed' || status === 'cancelled') return XCircle
  return Circle
}

function resourceIcon(resource: HarnessRuntimeResource) {
  if (resource.kind === 'budget') return Gauge
  if (resource.kind === 'mcp') return PlugZap
  if (resource.kind === 'agent') return Bot
  return Database
}

function transitionTaken(from: string, to?: string) {
  if (!to) return false
  return props.projection.transitions.some(
    (transition) => transition.from === from && transition.to === to && transition.takenCount > 0,
  )
}

function transitionActive(from: string, to?: string) {
  if (!to) return false
  return props.projection.transitions.some(
    (transition) => transition.from === from && transition.to === to && transition.active,
  )
}

const loopTransition = computed(() => props.projection.transitions.find(
  (transition) => transition.from === 'act' && transition.to === 'plan',
))

const focusStage = computed(() => {
  const active = props.projection.stages.find((stage) => stage.id === props.projection.activeStageId)
  if (active) return active
  return [...props.projection.stages].reverse().find((stage) => (
    stage.status === 'failed'
    || stage.status === 'cancelled'
    || stage.status === 'blocked'
    || stage.visitCount > 0
  ))
})

function stageStatusLabel(status: HarnessStageStatus) {
  if (status === 'running') return '执行中'
  if (status === 'blocked') return '等待确认'
  if (status === 'completed') return '完成'
  if (status === 'failed') return '失败'
  if (status === 'cancelled') return '已取消'
  return '未开始'
}

function formatDuration(duration?: number) {
  if (duration === undefined) return ''
  if (duration < 1_000) return `${duration}ms`
  if (duration < 60_000) return `${(duration / 1_000).toFixed(duration < 10_000 ? 1 : 0)}s`
  return `${Math.floor(duration / 60_000)}m ${Math.round(duration % 60_000 / 1_000)}s`
}
</script>

<template>
  <section class="harness-run-status" aria-label="Harness 运行路径">
    <header v-if="showHeader" class="run-status-header">
      <div>
        <span class="run-eyebrow">Harness</span>
        <strong>运行路径</strong>
      </div>
      <span class="run-state" :class="projection.status">{{ statusLabel }}</span>
    </header>

    <p v-if="projection.definitionMissing" class="definition-fallback">
      历史流程 {{ projection.definitionId }} v{{ projection.definitionVersion }} 已按事件顺序还原
    </p>

    <div class="stage-path-scroller">
      <ol
        class="stage-path"
        :style="{
          '--stage-count': projection.stages.length,
          '--stage-min-width': `${projection.stages.length * 106}px`,
        }"
      >
        <li
          v-for="(stage, index) in projection.stages"
          :key="stage.id"
          class="stage-step"
          :class="stage.status"
          :data-stage-id="stage.id"
        >
          <div
            class="stage-node"
            :aria-current="projection.activeStageId === stage.id ? 'step' : undefined"
          >
            <span class="stage-icon"><component :is="iconFor(stage.status)" :size="18" /></span>
            <span class="stage-copy">
              <strong>{{ stage.label }}</strong>
              <small v-if="stage.detail" :title="stage.detail">{{ stage.detail }}</small>
              <small v-else-if="stage.operationRef?.kind === 'knowledge_job'">
                {{ stage.operationRef.kind }} · {{ stage.operationRef.id }}
              </small>
            </span>
            <span v-if="stage.visitCount > 1 || stage.durationMs !== undefined" class="stage-meta">
              <span v-if="stage.visitCount > 1" class="visit-count"><RotateCcw :size="11" />{{ stage.visitCount }}</span>
              <span v-if="stage.durationMs !== undefined" class="stage-duration"><Clock3 :size="11" />{{ formatDuration(stage.durationMs) }}</span>
            </span>
          </div>
          <span
            v-if="index < projection.stages.length - 1"
            class="stage-edge"
            :class="{
              taken: transitionTaken(stage.id, projection.stages[index + 1]?.id),
              live: transitionActive(stage.id, projection.stages[index + 1]?.id),
            }"
            aria-hidden="true"
          ></span>
        </li>
      </ol>
    </div>

    <div v-if="focusStage" class="stage-focus" :class="focusStage.status">
      <component :is="iconFor(focusStage.status)" :size="15" />
      <span class="stage-focus-copy">
        <small>{{ projection.activeStageId ? '当前步骤' : '最近步骤' }}</small>
        <strong>{{ focusStage.label }}</strong>
      </span>
      <code v-if="focusStage.detail" :title="focusStage.detail">{{ focusStage.detail }}</code>
      <span class="stage-focus-state">{{ stageStatusLabel(focusStage.status) }}</span>
    </div>

    <div v-if="loopTransition" class="loop-return" :class="{ taken: loopTransition.takenCount > 0, live: loopTransition.active }">
      <RotateCcw :size="13" />
      <span>调用工具后继续规划</span>
      <strong v-if="loopTransition.takenCount">{{ loopTransition.takenCount }} 次</strong>
    </div>

    <section v-if="projection.runtimeResources?.length" class="resource-section" aria-label="Harness 运行资源">
      <header><strong>运行资源</strong><span>状态</span><span>详情</span></header>
      <ul class="runtime-resources">
        <li
          v-for="resource in projection.runtimeResources"
          :key="resource.id"
          :class="resource.status"
        >
          <component :is="resourceIcon(resource)" :size="15" />
          <strong>{{ resource.label }}</strong>
          <span class="resource-state">{{ resource.status === 'running' ? '运行中' : resource.status === 'blocked' ? '等待' : resource.status === 'failed' ? '失败' : '完成' }}</span>
          <span class="resource-detail" :title="resource.detail">{{ resource.detail }}</span>
        </li>
      </ul>
    </section>
  </section>
</template>

<style scoped>
.harness-run-status {
  width: 100%;
  min-width: 0;
  padding: 24px;
  color: var(--sage-text);
}

.run-status-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 38px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--sage-border);
}

.run-status-header > div { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.run-eyebrow { color: var(--sage-text-muted); font-size: var(--sage-font-xs); font-weight: 700; }
.run-status-header strong { color: var(--sage-text); font-size: var(--sage-font-md); }

.run-state {
  flex: none;
  padding: 4px 7px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius-sm);
  color: var(--sage-text-muted);
  background: var(--sage-surface);
  font-size: var(--sage-font-xs);
}

.run-state.running { color: var(--sage-success); border-color: color-mix(in srgb, var(--sage-success) 32%, var(--sage-border)); }
.run-state.blocked { color: var(--sage-warning); }
.run-state.failed,.run-state.cancelled { color: var(--sage-danger); }

.definition-fallback {
  margin: 0 0 14px;
  color: var(--sage-text-muted);
  font-size: var(--sage-font-xs);
}

.stage-path-scroller {
  width: 100%;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: thin;
  scrollbar-color: var(--sage-border-strong) transparent;
}

.stage-path {
  display: grid;
  grid-template-columns: repeat(var(--stage-count), minmax(96px, 1fr));
  gap: 0;
  min-width: var(--stage-min-width);
  margin: 0;
  padding: 6px 0 4px;
  list-style: none;
}

.stage-step { position: relative; min-width: 0; padding-right: 18px; }
.stage-step:last-child { padding-right: 0; }

.stage-node {
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: 1fr;
  justify-items: center;
  align-content: start;
  gap: 7px;
  min-height: 118px;
  padding: 0 6px 8px;
  border: 0;
  color: var(--sage-text-muted);
  background: transparent;
}

.stage-icon {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border: 1px solid var(--sage-border-strong);
  border-radius: 50%;
  color: var(--sage-text-muted);
  background: var(--sage-surface-raised);
}

.stage-step.running .stage-icon,
.stage-node[aria-current="step"] .stage-icon {
  border-color: var(--sage-success);
  color: var(--sage-success);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--sage-success) 10%, transparent);
}

.stage-step.completed .stage-icon { border-color: color-mix(in srgb, var(--sage-success) 58%, var(--sage-border)); color: var(--sage-success); }
.stage-step.blocked .stage-icon { border-color: var(--sage-warning); color: var(--sage-warning); }
.stage-step.failed .stage-icon,.stage-step.cancelled .stage-icon { border-color: var(--sage-danger); color: var(--sage-danger); }
.stage-step.running .stage-icon > svg { animation: stage-spin 1.2s linear infinite; }

.stage-copy { display: grid; justify-items: center; gap: 3px; width: 100%; min-width: 0; text-align: center; }
.stage-copy strong { overflow: hidden; color: var(--sage-text); font-size: var(--sage-font-sm); text-overflow: ellipsis; white-space: nowrap; }
.stage-copy strong,
.stage-copy small { display: block; width: 100%; max-width: 100%; }
.stage-copy small { overflow: hidden; color: var(--sage-text-muted); font-family: var(--sage-font-mono); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }

.stage-meta,
.visit-count,.stage-duration {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--sage-text-muted);
  font-size: var(--sage-font-xs);
}

.stage-meta { justify-content: center; gap: 8px; min-height: 17px; }

.stage-edge {
  position: absolute;
  z-index: 1;
  top: 22px;
  right: 0;
  width: 18px;
  height: 1px;
  background: var(--sage-border-strong);
}

.stage-edge::after {
  position: absolute;
  top: -3px;
  right: 0;
  width: 6px;
  height: 6px;
  border-top: 1px solid currentColor;
  border-right: 1px solid currentColor;
  color: var(--sage-border-strong);
  transform: rotate(45deg);
  content: '';
}

.stage-edge.taken { background: var(--sage-success); }
.stage-edge.taken::after { color: var(--sage-success); }
.stage-edge.live::before {
  position: absolute;
  z-index: 2;
  top: -2px;
  left: 0;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--sage-success);
  animation: stage-flow 1s linear infinite;
  content: '';
}

.stage-focus {
  display: grid;
  grid-template-columns: 18px auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-height: 46px;
  margin: 4px 0 0;
  padding: 7px 11px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius-sm);
  color: var(--sage-text-muted);
  background: var(--sage-surface-muted);
}
.stage-focus > svg { color: var(--sage-text-muted); }
.stage-focus.running > svg,.stage-focus.completed > svg { color: var(--sage-success); }
.stage-focus.blocked > svg { color: var(--sage-warning); }
.stage-focus.failed > svg,.stage-focus.cancelled > svg { color: var(--sage-danger); }
.stage-focus.running > svg { animation: stage-spin 1.2s linear infinite; }
.stage-focus-copy { display: grid; min-width: 0; }
.stage-focus-copy small { color: var(--sage-text-muted); font-size: 10px; }
.stage-focus-copy strong { color: var(--sage-text-secondary); font-size: var(--sage-font-sm); }
.stage-focus code {
  min-width: 0;
  overflow: hidden;
  color: var(--sage-text-secondary);
  font-family: var(--sage-font-mono);
  font-size: var(--sage-font-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stage-focus-state { color: var(--sage-text-muted); font-size: var(--sage-font-xs); white-space: nowrap; }
.stage-focus.running .stage-focus-state,.stage-focus.completed .stage-focus-state { color: var(--sage-success); }
.stage-focus.blocked .stage-focus-state { color: var(--sage-warning); }
.stage-focus.failed .stage-focus-state,.stage-focus.cancelled .stage-focus-state { color: var(--sage-danger); }

.loop-return {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: max-content;
  max-width: 100%;
  margin: 12px auto 18px;
  color: var(--sage-text-muted);
  font-size: var(--sage-font-xs);
}
.loop-return.taken { color: var(--sage-success); }
.loop-return strong { font-family: var(--sage-font-mono); font-weight: 500; }
.loop-return.live svg { animation: stage-spin 1.2s linear infinite; }

.resource-section {
  margin-top: 18px;
  border-top: 1px solid var(--sage-border);
}
.resource-section > header,
.runtime-resources li {
  display: grid;
  grid-template-columns: minmax(130px, .8fr) 84px minmax(180px, 1.4fr);
  align-items: center;
  gap: 12px;
}
.resource-section > header {
  min-height: 38px;
  color: var(--sage-text-muted);
  font-size: 10px;
}
.resource-section > header strong { color: var(--sage-text-secondary); font-size: var(--sage-font-xs); }
.runtime-resources { margin: 0; padding: 0; list-style: none; }
.runtime-resources li {
  position: relative;
  min-height: 44px;
  padding-left: 28px;
  border-top: 1px solid var(--sage-border);
  color: var(--sage-text-muted);
  font-size: var(--sage-font-xs);
}
.runtime-resources li > svg { position: absolute; left: 2px; color: var(--sage-success); }
.runtime-resources li.blocked > svg { color: var(--sage-warning); }
.runtime-resources li.failed > svg,.runtime-resources li.cancelled > svg { color: var(--sage-danger); }
.runtime-resources strong { overflow: hidden; color: var(--sage-text-secondary); text-overflow: ellipsis; white-space: nowrap; }
.resource-state { color: var(--sage-success); }
.runtime-resources li.blocked .resource-state { color: var(--sage-warning); }
.runtime-resources li.failed .resource-state,.runtime-resources li.cancelled .resource-state { color: var(--sage-danger); }
.resource-detail { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

@keyframes stage-spin { to { transform: rotate(360deg); } }
@keyframes stage-flow { to { transform: translateX(14px); } }

@media (max-width: 760px) {
  .harness-run-status { padding: 16px; }
  .stage-path-scroller { overflow: visible; }
  .stage-path { display: grid; grid-template-columns: 1fr !important; gap: 18px; min-width: 0; margin-top: 6px; }
  .stage-step { padding-right: 0; }
  .stage-node { min-height: 108px; }
  .stage-edge { top: auto; right: auto; bottom: -18px; left: 50%; width: 1px; height: 18px; }
  .stage-edge::after { top: auto; right: -3px; bottom: 1px; transform: rotate(135deg); }
  .stage-edge.live::before { display: none; }
  .stage-focus { grid-template-columns: 18px minmax(0, 1fr) auto; }
  .stage-focus code { grid-column: 2 / -1; }
  .resource-section > header { display: none; }
  .runtime-resources li { grid-template-columns: minmax(0, 1fr) auto; gap: 4px 10px; padding: 10px 0 10px 28px; }
  .runtime-resources li > svg { top: 12px; }
  .resource-detail { grid-column: 1 / -1; }
}

@media (prefers-reduced-motion: reduce) {
  .stage-step.running .stage-icon > svg,
  .stage-edge.live::before,
  .stage-focus.running > svg,
  .loop-return.live svg { animation: none; }
}
</style>
