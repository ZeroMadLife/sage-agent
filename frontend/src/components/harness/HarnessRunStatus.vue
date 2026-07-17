<script setup lang="ts">
import {
  Bot,
  CheckCircle2,
  Circle,
  Clock3,
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

const props = defineProps<{
  projection: HarnessProjection
}>()

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

function formatDuration(duration?: number) {
  if (duration === undefined) return ''
  if (duration < 1_000) return `${duration}ms`
  if (duration < 60_000) return `${(duration / 1_000).toFixed(duration < 10_000 ? 1 : 0)}s`
  return `${Math.floor(duration / 60_000)}m ${Math.round(duration % 60_000 / 1_000)}s`
}
</script>

<template>
  <section class="harness-run-status" aria-label="Harness 运行路径">
    <header class="run-status-header">
      <div>
        <span class="run-eyebrow">Harness</span>
        <strong>运行路径</strong>
      </div>
      <span class="run-state" :class="projection.status">{{ statusLabel }}</span>
    </header>

    <p v-if="projection.definitionMissing" class="definition-fallback">
      历史流程 {{ projection.definitionId }} v{{ projection.definitionVersion }} 已按事件顺序还原
    </p>

    <ul v-if="projection.runtimeResources?.length" class="runtime-resources" aria-label="Harness 运行资源">
      <li
        v-for="resource in projection.runtimeResources"
        :key="resource.id"
        :class="resource.status"
      >
        <component :is="resourceIcon(resource)" :size="14" />
        <strong>{{ resource.label }}</strong>
        <span :title="resource.detail">{{ resource.detail }}</span>
      </li>
    </ul>

    <ol class="stage-path" :class="{ 'with-resources': projection.runtimeResources?.length }">
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
          <component :is="iconFor(stage.status)" :size="17" />
          <span class="stage-copy">
            <strong>{{ stage.label }}</strong>
            <small v-if="stage.detail" :title="stage.detail">{{ stage.detail }}</small>
            <small v-else-if="stage.operationRef?.kind === 'knowledge_job'">
              {{ stage.operationRef.kind }} · {{ stage.operationRef.id }}
            </small>
          </span>
          <span v-if="stage.visitCount > 1" class="visit-count"><RotateCcw :size="11" />{{ stage.visitCount }}</span>
          <span v-if="stage.durationMs !== undefined" class="stage-duration"><Clock3 :size="11" />{{ formatDuration(stage.durationMs) }}</span>
        </div>
        <span
          v-if="index < projection.stages.length - 1"
          class="stage-edge"
          :class="{ taken: transitionTaken(stage.id, projection.stages[index + 1]?.id) }"
          aria-hidden="true"
        ></span>
      </li>
    </ol>
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
  margin: 14px 0 0;
  color: var(--sage-text-muted);
  font-size: var(--sage-font-xs);
}

.runtime-resources {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  margin: 13px 0 0;
  padding: 0;
  color: var(--sage-text-muted);
  list-style: none;
}

.runtime-resources li {
  display: grid;
  grid-template-columns: 16px auto minmax(0, 1fr);
  align-items: center;
  gap: 5px;
  min-width: 0;
  max-width: min(100%, 360px);
  font-size: var(--sage-font-xs);
}

.runtime-resources li > svg { color: var(--sage-success); }
.runtime-resources li.blocked > svg { color: var(--sage-warning); }
.runtime-resources li.failed > svg,.runtime-resources li.cancelled > svg { color: var(--sage-danger); }
.runtime-resources strong { color: var(--sage-text-secondary); white-space: nowrap; }
.runtime-resources span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.stage-path {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(116px, 1fr));
  gap: 0;
  margin: 34px 0 0;
  padding: 0;
  list-style: none;
}

.stage-path.with-resources { margin-top: 20px; }

.stage-step { position: relative; min-width: 0; padding-right: 22px; }
.stage-step:last-child { padding-right: 0; }

.stage-node {
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px;
  min-height: 72px;
  padding: 11px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  color: var(--sage-text-muted);
  background: var(--sage-surface);
}

.stage-step.running .stage-node,
.stage-node[aria-current="step"] {
  border-color: var(--sage-success);
  color: var(--sage-success);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--sage-success) 10%, transparent);
}

.stage-step.completed .stage-node { color: var(--sage-success); }
.stage-step.blocked .stage-node { border-color: var(--sage-warning); color: var(--sage-warning); }
.stage-step.failed .stage-node,.stage-step.cancelled .stage-node { border-color: var(--sage-danger); color: var(--sage-danger); }
.stage-step.running .stage-node > svg { animation: stage-spin 1.2s linear infinite; }

.stage-copy { display: grid; gap: 4px; min-width: 0; }
.stage-copy strong { overflow: hidden; color: var(--sage-text); font-size: var(--sage-font-sm); text-overflow: ellipsis; white-space: nowrap; }
.stage-copy small { overflow: hidden; color: var(--sage-text-muted); font-family: var(--sage-font-mono); font-size: var(--sage-font-xs); text-overflow: ellipsis; white-space: nowrap; }

.visit-count,.stage-duration {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--sage-text-muted);
  font-size: var(--sage-font-xs);
}

.visit-count { grid-column: 1 / -1; }
.stage-duration { position: absolute; right: 8px; bottom: 7px; }

.stage-edge {
  position: absolute;
  z-index: 1;
  top: 35px;
  right: 0;
  width: 22px;
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

@keyframes stage-spin { to { transform: rotate(360deg); } }

@media (max-width: 760px) {
  .harness-run-status { padding: 16px; }
  .stage-path { display: grid; grid-template-columns: 1fr; gap: 18px; margin-top: 22px; }
  .stage-step { padding-right: 0; }
  .stage-edge { top: auto; right: auto; bottom: -18px; left: 22px; width: 1px; height: 18px; }
  .stage-edge::after { top: auto; right: -3px; bottom: 1px; transform: rotate(135deg); }
}

@media (prefers-reduced-motion: reduce) {
  .stage-step.running .stage-node > svg { animation: none; }
}
</style>
