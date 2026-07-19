<script setup lang="ts">
import {
  CheckCircle2,
  Circle,
  LoaderCircle,
  PauseCircle,
  PlugZap,
  RotateCcw,
  XCircle,
} from 'lucide-vue-next'
import { computed } from 'vue'
import type { CodingConnectionState } from '../../stores/codingStream'
import type { HarnessProjection, HarnessSurfaceContext } from '../../harness/types'
import { harnessRunVisualState } from '../../harness/runVisualState'
import ChatTimeline, { type ChatScrollAnchor } from './chat/ChatTimeline.vue'

const props = withDefaults(defineProps<{
  projection: HarnessProjection
  connectionState: CodingConnectionState
  context?: HarnessSurfaceContext | null
  outputSignature: string
  messageCount: number
  sessionKey: string
  timelineReady: boolean
  scrollAnchor?: ChatScrollAnchor | null
  hasMore?: boolean
  loading?: boolean
  isEmpty?: boolean
  compactStatus?: boolean
  surfaceLabel?: string
  emptyTitle?: string
  emptyDescription?: string
  loadOlder?: () => Promise<void>
}>(), {
  context: null,
  scrollAnchor: null,
  hasMore: false,
  loading: false,
  isEmpty: false,
  compactStatus: false,
  surfaceLabel: '',
  emptyTitle: 'Sage 已就绪',
  emptyDescription: '输入目标，Sage 会保留本轮运行证据。',
  loadOlder: undefined,
})

const emit = defineEmits<{
  anchorChange: [eventId: string, offset: number]
}>()

const visualState = computed(() => harnessRunVisualState(
  props.projection,
  props.connectionState,
))
const stateMeta = computed(() => ({
  idle: { label: '未开始', icon: Circle },
  running: { label: '运行中', icon: LoaderCircle },
  tool: { label: '调用工具', icon: PlugZap },
  approval: { label: '等待审批', icon: PauseCircle },
  failed: { label: '运行失败', icon: XCircle },
  completed: { label: '已完成', icon: CheckCircle2 },
  recovering: { label: '恢复连接', icon: RotateCcw },
}[visualState.value]))
const contextLabel = computed(() => (
  props.context?.selection?.label
  || props.context?.resource?.label
  || (props.context?.surface === 'knowledge' ? 'Knowledge 图谱' : '当前工作区')
))
const contextReceipt = computed(() => {
  if (!props.context || props.context.surface !== 'knowledge') return ''
  const parts: string[] = []
  const shortRef = (value: string) => value.length > 12 ? `${value.slice(0, 12)}...` : value
  const revisionRef = (revision?: string) => revision ? `@${shortRef(revision)}` : ''
  if (props.context.graphRevision) parts.push(`graph ${shortRef(props.context.graphRevision)}`)
  if (props.context.selection) {
    const type = props.context.selection.type === 'graph_node' ? 'node' : props.context.selection.type
    parts.push(`${type} ${shortRef(props.context.selection.id)}${revisionRef(props.context.selection.revision)}`)
  }
  if (props.context.resource) {
    const type = props.context.resource.type === 'knowledge_page' ? 'page' : 'source'
    parts.push(`${type} ${shortRef(props.context.resource.id)}${revisionRef(props.context.resource.revision)}`)
  }
  return parts.join(' · ')
})
const completedStages = computed(() => props.projection.stages.filter(
  (stage) => stage.status === 'completed',
).length)

function handleAnchorChange(eventId: string, offset: number) {
  emit('anchorChange', eventId, offset)
}
</script>

<template>
  <section
    class="chat-dock"
    :class="{ compact: compactStatus }"
    :data-run-state="visualState"
    aria-label="Chat Harness"
  >
    <header class="chat-run-strip">
      <div class="run-strip-title">
        <component :is="stateMeta.icon" :size="15" />
        <span><strong>{{ stateMeta.label }}</strong><small v-if="!compactStatus">来自 timeline 与连接状态</small></span>
        <code v-if="projection.runId">{{ projection.runId.slice(0, 12) }}</code>
      </div>
      <div class="run-stage-track" :aria-label="`${completedStages}/${projection.stages.length} 个阶段完成`">
        <i
          v-for="stage in projection.stages"
          :key="stage.id"
          :class="stage.status"
          :title="`${stage.label} · ${stage.status}`"
        ></i>
      </div>
    </header>

    <aside v-if="$slots.contextTools" class="chat-context-tools" aria-label="当前上下文操作">
      <slot name="contextTools" />
    </aside>

    <aside v-if="$slots.attention" class="chat-attention" aria-label="需要你处理">
      <slot name="attention" />
    </aside>

    <ChatTimeline
      class="chat-dock-timeline message-area"
      :output-signature="outputSignature"
      :message-count="messageCount"
      :session-key="sessionKey"
      :timeline-ready="timelineReady"
      :scroll-anchor="scrollAnchor"
      :has-more="hasMore"
      :loading="loading"
      :is-empty="isEmpty"
      :load-older="loadOlder"
      @anchor-change="handleAnchorChange"
    >
      <template #empty>
        <slot name="empty">
          <div class="chat-empty-state">
            <strong>{{ emptyTitle }}</strong>
            <span>{{ emptyDescription }}</span>
          </div>
        </slot>
      </template>
      <slot />
    </ChatTimeline>

    <div class="chat-dock-composer"><slot name="composer" /></div>
    <footer v-if="context" class="surface-context-bar">
      <span class="surface-context-copy">
        <b>{{ surfaceLabel || context.surface }} · {{ contextLabel }}</b>
        <small v-if="contextReceipt">{{ contextReceipt }}</small>
      </span>
      <code>surface_context 提交时冻结</code>
    </footer>
  </section>
</template>

<style scoped>
.chat-dock {
  --chat-content-max: 100%;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto auto;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  color: var(--sage-text);
  background: var(--sage-surface);
}
.chat-dock:has(.chat-attention) { grid-template-rows: auto auto minmax(0, 1fr) auto auto; }
.chat-dock:has(.chat-context-tools) { grid-template-rows: auto auto minmax(0, 1fr) auto auto; }
.chat-dock:has(.chat-context-tools):has(.chat-attention) { grid-template-rows: auto auto auto minmax(0, 1fr) auto auto; }

.chat-run-strip { padding: 10px 12px 9px; border-bottom: 1px solid var(--sage-border); background: var(--sage-surface-muted); }
.chat-dock.compact .chat-run-strip { padding-top: 8px; padding-bottom: 7px; }
.run-strip-title { display: flex; align-items: center; gap: 8px; min-width: 0; color: var(--sage-text-muted); }
.run-strip-title > svg { flex: none; }
.run-strip-title > span { display: flex; align-items: baseline; gap: 6px; min-width: 0; }
.run-strip-title strong { color: var(--sage-text-secondary); font-size: var(--sage-font-sm); }
.run-strip-title small { color: var(--sage-text-muted); font-size: 10px; }
.run-strip-title code { min-width: 0; margin-left: auto; overflow: hidden; color: var(--sage-text-muted); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.chat-dock[data-run-state="running"] .run-strip-title > svg,
.chat-dock[data-run-state="tool"] .run-strip-title > svg,
.chat-dock[data-run-state="completed"] .run-strip-title > svg { color: var(--sage-success); }
.chat-dock[data-run-state="approval"] .run-strip-title > svg { color: var(--sage-warning); }
.chat-dock[data-run-state="failed"] .run-strip-title > svg { color: var(--sage-danger); }
.chat-dock[data-run-state="recovering"] .run-strip-title > svg { color: var(--sage-source); }
.chat-dock[data-run-state="running"] .run-strip-title > svg,
.chat-dock[data-run-state="recovering"] .run-strip-title > svg { animation: dock-spin 1.2s linear infinite; }

.run-stage-track { display: grid; grid-template-columns: repeat(auto-fit, minmax(14px, 1fr)); gap: 4px; height: 3px; margin-top: 8px; }
.run-stage-track i { min-width: 0; border-radius: 2px; background: var(--sage-border); }
.run-stage-track i.completed { background: color-mix(in srgb, var(--sage-success) 68%, var(--sage-border)); }
.run-stage-track i.running { background: var(--sage-success); }
.run-stage-track i.blocked { background: var(--sage-warning); }
.run-stage-track i.failed,.run-stage-track i.cancelled { background: var(--sage-danger); }
.chat-context-tools { min-width:0; max-height:min(32dvh,250px); overflow:auto; border-bottom:1px solid var(--sage-border); background:var(--sage-surface); }
.chat-attention { min-width: 0; max-height:min(44dvh,420px); overflow:auto; border-bottom: 1px solid color-mix(in srgb, var(--sage-warning) 48%, var(--sage-border)); background: var(--sage-warning-bg); }
.chat-dock-timeline { padding: 14px 12px 16px; }
.chat-dock-composer { min-width: 0; }
.surface-context-bar { display: flex; align-items: center; gap: 10px; min-width: 0; min-height: 28px; padding: 5px 11px; border-top: 1px solid var(--sage-border); color: var(--sage-text-muted); font-size: 10px; }
.surface-context-copy { display: grid; min-width: 0; gap: 2px; }
.surface-context-copy b,.surface-context-copy small { min-width: 0; overflow: hidden; font: inherit; text-overflow: ellipsis; white-space: nowrap; }
.surface-context-copy b { color: var(--sage-text-secondary); }
.surface-context-copy small { color: var(--sage-text-muted); }
.surface-context-bar code { margin-left: auto; color: var(--sage-text-muted); white-space: nowrap; }
.chat-empty-state { display: flex; min-height: 100%; flex-direction: column; align-items: center; justify-content: center; gap: 5px; color: var(--sage-text-muted); text-align: center; }
.chat-empty-state strong { color: var(--sage-text-secondary); font-size: var(--sage-font-md); }
.chat-empty-state span { max-width: 260px; font-size: var(--sage-font-sm); line-height: 1.5; }
@keyframes dock-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .run-strip-title > svg { animation: none !important; } }
</style>
