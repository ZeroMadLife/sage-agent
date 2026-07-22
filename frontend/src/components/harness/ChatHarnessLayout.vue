<script setup lang="ts">
import {
  ChevronLeft,
  MessageSquareText,
  PanelRight,
  PanelRightOpen,
  PanelsTopLeft,
} from 'lucide-vue-next'
import { computed, nextTick, onBeforeUnmount, ref, useId } from 'vue'

export type WorkbenchTab = 'chat' | 'details'
type MobilePane = 'canvas' | WorkbenchTab
type HarnessLayoutMode = 'canvas-dock' | 'conversation-facts'

const props = withDefaults(defineProps<{
  surfaceLabel?: string
  defaultDockWidth?: number
  minDockWidth?: number
  maxDockWidth?: number
  initialTab?: WorkbenchTab
  initialMobilePane?: MobilePane
  showDetails?: boolean
  chatLabel?: string
  mode?: HarnessLayoutMode
  factsLabel?: string
  initialDockOpen?: boolean
}>(), {
  surfaceLabel: 'Sage',
  defaultDockWidth: 420,
  minDockWidth: 360,
  maxDockWidth: 520,
  initialTab: 'chat',
  initialMobilePane: 'canvas',
  showDetails: true,
  chatLabel: '对话',
  mode: 'canvas-dock',
  factsLabel: '本轮事实',
  initialDockOpen: true,
})

const emit = defineEmits<{
  tabChange: [tab: WorkbenchTab]
  dockChange: [open: boolean]
}>()

const layoutId = useId()
const openChatButton = ref<HTMLButtonElement | null>(null)
const firstTabButton = ref<HTMLButtonElement | null>(null)

function clampWidth(value: number) {
  return Math.min(props.maxDockWidth, Math.max(props.minDockWidth, value))
}

function initialDockWidth() {
  if (typeof window === 'undefined') return clampWidth(props.defaultDockWidth)
  const saved = Number.parseInt(localStorage.getItem('sage.harness.dockWidth') || '', 10)
  return clampWidth(Number.isFinite(saved) ? saved : props.defaultDockWidth)
}

const dockWidth = ref(initialDockWidth())
const dockOpen = ref(props.initialDockOpen)
const activeTab = ref<WorkbenchTab>(props.initialTab)
const mobilePane = ref<MobilePane>(props.initialMobilePane)
const resizing = ref(false)
const layoutStyle = computed(() => ({ '--harness-dock-width': `${dockWidth.value}px` }))

function persistDockWidth() {
  if (typeof window !== 'undefined') {
    localStorage.setItem('sage.harness.dockWidth', String(dockWidth.value))
  }
}

function selectTab(tab: WorkbenchTab, focus = false) {
  if (tab === 'details' && !props.showDetails) tab = 'chat'
  activeTab.value = tab
  mobilePane.value = tab
  dockOpen.value = true
  emit('tabChange', tab)
  emit('dockChange', true)
  if (focus) {
    void nextTick(() => {
      document.getElementById(`${layoutId}-${tab}-tab`)?.focus()
    })
  }
}

function showCanvas() {
  mobilePane.value = 'canvas'
}

function collapseDock() {
  dockOpen.value = false
  mobilePane.value = 'canvas'
  emit('dockChange', false)
  void nextTick(() => openChatButton.value?.focus())
}

function openDock(tab: WorkbenchTab = 'chat') {
  if (tab === 'details' && !props.showDetails) tab = 'chat'
  dockOpen.value = true
  activeTab.value = tab
  mobilePane.value = tab
  emit('tabChange', tab)
  emit('dockChange', true)
  void nextTick(() => firstTabButton.value?.focus())
}

function openFacts() {
  dockOpen.value = true
  emit('dockChange', true)
}

function closeFacts() {
  dockOpen.value = false
  emit('dockChange', false)
}

function handleTabKeydown(event: KeyboardEvent, tab: WorkbenchTab) {
  let nextTab: WorkbenchTab | null = null
  if (event.key === 'ArrowRight' || event.key === 'End') nextTab = 'details'
  else if (event.key === 'ArrowLeft' || event.key === 'Home') nextTab = 'chat'
  if (!nextTab || nextTab === tab && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) return
  event.preventDefault()
  selectTab(nextTab, true)
}

function handleResizeMove(event: PointerEvent) {
  dockWidth.value = clampWidth(window.innerWidth - event.clientX)
}

function stopResize() {
  if (!resizing.value) return
  resizing.value = false
  window.removeEventListener('pointermove', handleResizeMove)
  window.removeEventListener('pointerup', stopResize)
  persistDockWidth()
}

function startResize(event: PointerEvent) {
  if (event.button !== 0) return
  event.preventDefault()
  resizing.value = true
  window.addEventListener('pointermove', handleResizeMove)
  window.addEventListener('pointerup', stopResize)
}

function handleResizeKeydown(event: KeyboardEvent) {
  let nextWidth = dockWidth.value
  if (event.key === 'ArrowLeft') nextWidth += 16
  else if (event.key === 'ArrowRight') nextWidth -= 16
  else if (event.key === 'Home') nextWidth = props.minDockWidth
  else if (event.key === 'End') nextWidth = props.maxDockWidth
  else return

  event.preventDefault()
  dockWidth.value = clampWidth(nextWidth)
  persistDockWidth()
}

onBeforeUnmount(stopResize)

defineExpose({
  selectTab,
  showCanvas,
  openFacts,
  closeFacts,
})
</script>

<template>
  <div
    class="chat-harness-layout"
    :class="{ resizing }"
    :style="layoutStyle"
    :data-mode="mode"
    :data-dock-open="dockOpen"
    :data-active-tab="activeTab"
    :data-mobile-pane="mobilePane"
    :data-has-details="showDetails"
  >
    <template v-if="mode === 'conversation-facts'">
      <section class="conversation-primary" :aria-label="`${surfaceLabel} 对话`">
        <slot name="chat" />
      </section>

      <button v-if="dockOpen" class="facts-backdrop" type="button" aria-label="关闭事实栏" @click="closeFacts"></button>

      <aside v-if="dockOpen" class="facts-dock" :aria-label="factsLabel">
        <div
          class="dock-resize-handle"
          role="separator"
          aria-label="调整事实栏宽度"
          aria-orientation="vertical"
          :aria-valuemin="minDockWidth"
          :aria-valuemax="maxDockWidth"
          :aria-valuenow="dockWidth"
          tabindex="0"
          @pointerdown="startResize"
          @keydown="handleResizeKeydown"
        ></div>
        <header class="facts-dock-header"><span><small>Facts</small><strong>{{ factsLabel }}</strong></span><button type="button" aria-label="收起事实栏" title="收起事实栏" @click="closeFacts"><ChevronLeft :size="16" /></button></header>
        <section class="facts-dock-content"><slot name="facts" :close="closeFacts" /></section>
      </aside>

      <aside v-else class="facts-closed-rail" aria-label="打开事实栏"><button type="button" aria-label="打开事实栏" title="打开事实栏" @click="openFacts"><PanelRightOpen :size="17" /></button></aside>
    </template>

    <template v-else>
      <section class="harness-canvas" :aria-label="`${surfaceLabel} 主画布`">
        <slot name="canvas" />
      </section>

      <button v-if="dockOpen" class="dock-backdrop" type="button" aria-label="关闭工作台" @click="collapseDock"></button>

      <aside v-if="dockOpen" class="workbench-dock" aria-label="工作台">
      <div
        class="dock-resize-handle"
        role="separator"
        aria-label="调整工作台宽度"
        aria-orientation="vertical"
        :aria-valuemin="minDockWidth"
        :aria-valuemax="maxDockWidth"
        :aria-valuenow="dockWidth"
        tabindex="0"
        @pointerdown="startResize"
        @keydown="handleResizeKeydown"
      ></div>

      <header class="dock-header">
        <div class="dock-tabs" role="tablist" aria-label="工作台视图">
          <button
            ref="firstTabButton"
            :id="`${layoutId}-chat-tab`"
            type="button"
            role="tab"
            :aria-selected="activeTab === 'chat'"
            :aria-controls="`${layoutId}-chat-panel`"
            :tabindex="activeTab === 'chat' ? 0 : -1"
            @click="selectTab('chat')"
            @keydown="handleTabKeydown($event, 'chat')"
          ><MessageSquareText :size="14" />{{ chatLabel }}</button>
          <button
            v-if="showDetails"
            :id="`${layoutId}-details-tab`"
            type="button"
            role="tab"
            :aria-selected="activeTab === 'details'"
            :aria-controls="`${layoutId}-details-panel`"
            :tabindex="activeTab === 'details' ? 0 : -1"
            @click="selectTab('details')"
            @keydown="handleTabKeydown($event, 'details')"
          ><PanelRight :size="14" />详情</button>
        </div>
        <button class="dock-collapse" type="button" title="收起工作台" aria-label="收起工作台" @click="collapseDock">
          <ChevronLeft :size="16" />
        </button>
      </header>

      <section
        v-show="activeTab === 'chat'"
        :id="`${layoutId}-chat-panel`"
        class="dock-panel"
        role="tabpanel"
        :aria-labelledby="`${layoutId}-chat-tab`"
      ><slot name="chat" /></section>
      <section
        v-if="showDetails"
        v-show="activeTab === 'details'"
        :id="`${layoutId}-details-panel`"
        class="dock-panel"
        role="tabpanel"
        :aria-labelledby="`${layoutId}-details-tab`"
      ><slot name="details" /></section>
      </aside>

      <aside v-else class="dock-rail" aria-label="打开工作台">
      <button ref="openChatButton" type="button" title="打开对话" aria-label="打开对话工作台" @click="openDock('chat')">
        <MessageSquareText :size="17" />
      </button>
      <button v-if="showDetails" type="button" title="打开详情" aria-label="打开详情工作台" @click="openDock('details')">
        <PanelRight :size="17" />
      </button>
      </aside>

      <nav class="mobile-surface-nav" aria-label="移动工作台视图">
      <button type="button" :aria-pressed="mobilePane === 'canvas'" @click="showCanvas"><PanelsTopLeft :size="16" />画布</button>
      <button type="button" :aria-pressed="mobilePane === 'chat'" @click="selectTab('chat')"><MessageSquareText :size="16" />{{ chatLabel }}</button>
      <button v-if="showDetails" type="button" :aria-pressed="mobilePane === 'details'" @click="selectTab('details')"><PanelRight :size="16" />详情</button>
      </nav>
    </template>
  </div>
</template>

<style scoped>
.chat-harness-layout {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) var(--harness-dock-width);
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--sage-surface-muted);
}

.chat-harness-layout[data-dock-open="false"] { grid-template-columns: minmax(0, 1fr) 42px; }
.chat-harness-layout.resizing { cursor: col-resize; user-select: none; }

.chat-harness-layout[data-mode="conversation-facts"] { grid-template-columns: minmax(620px, 1fr) var(--harness-dock-width); background: var(--sage-surface); }
.chat-harness-layout[data-mode="conversation-facts"][data-dock-open="false"] { grid-template-columns: minmax(620px, 1fr) 42px; }
.conversation-primary { min-width: 0; min-height: 0; overflow: hidden; background: var(--sage-surface); }
.conversation-primary > :deep(*) { height: 100%; min-height: 0; }
.facts-dock { position: relative; z-index: 3; display: grid; grid-template-rows: 52px minmax(0, 1fr); min-width: 0; min-height: 0; overflow: hidden; border-left: 1px solid var(--sage-border); background: var(--sage-surface); }
.facts-dock-header { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 0 9px 0 14px; border-bottom: 1px solid var(--sage-border); }.facts-dock-header > span { display: grid; gap: 1px; }.facts-dock-header small { color: var(--sage-brand-strong); font-size: 9px; font-weight: 700; text-transform: uppercase; }.facts-dock-header strong { font-size: var(--sage-font-sm); }.facts-dock-header button,.facts-closed-rail button { display: grid; place-items: center; width: 30px; height: 30px; padding: 0; border: 0; border-radius: var(--sage-radius-sm); color: var(--sage-text-muted); background: transparent; }.facts-dock-header button:hover,.facts-closed-rail button:hover { color: var(--sage-text); background: var(--sage-surface-muted); }
.facts-dock-content { min-width: 0; min-height: 0; overflow: hidden; }.facts-dock-content > :deep(*) { height: 100%; min-height: 0; }
.facts-closed-rail { z-index: 2; display: flex; justify-content: center; padding-top: 10px; border-left: 1px solid var(--sage-border); background: var(--sage-surface); }
.facts-backdrop { display: none; }

.harness-canvas {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  background: var(--sage-surface-muted);
}

.workbench-dock {
  position: relative;
  z-index: 3;
  display: grid;
  grid-template-rows: 42px minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-left: 1px solid var(--sage-border);
  background: var(--sage-surface);
}

.dock-resize-handle {
  position: absolute;
  z-index: 5;
  inset: 0 auto 0 -4px;
  width: 8px;
  cursor: col-resize;
}

.dock-resize-handle::after {
  position: absolute;
  inset: 0 auto 0 3px;
  width: 1px;
  background: transparent;
  content: '';
}

.dock-resize-handle:hover::after,
.dock-resize-handle:focus-visible::after { background: var(--sage-focus); }
.dock-resize-handle:focus-visible { outline: none; }

.dock-header {
  display: flex;
  align-items: center;
  min-width: 0;
  padding: 0 6px 0 10px;
  border-bottom: 1px solid var(--sage-border);
}

.dock-tabs { display: flex; align-items: stretch; height: 100%; min-width: 0; }
.dock-tabs button {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 72px;
  padding: 0 10px;
  border: 0;
  border-bottom: 2px solid transparent;
  color: var(--sage-text-muted);
  background: transparent;
  font-size: var(--sage-font-xs);
  font-weight: 650;
}

.dock-tabs button[aria-selected="true"] {
  border-bottom-color: var(--sage-success);
  color: var(--sage-text);
}

.dock-tabs button:focus-visible { outline: 2px solid var(--sage-focus); outline-offset: -3px; }

.dock-collapse,
.dock-rail button {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--sage-radius-sm);
  color: var(--sage-text-muted);
  background: transparent;
}

.dock-collapse { margin-left: auto; }
.dock-collapse:hover,
.dock-rail button:hover { border-color: var(--sage-border); color: var(--sage-text); background: var(--sage-surface-muted); }

.dock-panel { min-width: 0; min-height: 0; overflow: hidden; }
.dock-panel > :deep(*) { height: 100%; min-height: 0; }

.dock-rail {
  z-index: 2;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding-top: 8px;
  border-left: 1px solid var(--sage-border);
  background: var(--sage-surface);
}

.dock-backdrop,
.mobile-surface-nav { display: none; }

@media (max-width: 1100px) and (min-width: 768px) {
  .chat-harness-layout { display: block; }
  .harness-canvas { width: 100%; height: 100%; }
  .workbench-dock {
    position: absolute;
    inset: 0 0 0 auto;
    width: min(var(--harness-dock-width), 92%);
    box-shadow: var(--sage-shadow-drawer);
  }
  .dock-backdrop {
    position: absolute;
    z-index: 2;
    inset: 0;
    display: block;
    width: 100%;
    height: 100%;
    padding: 0;
    border: 0;
    background: rgb(17 18 20 / 36%);
  }
  .dock-rail { position: absolute; z-index: 3; inset: 0 0 0 auto; width: 42px; }
  .chat-harness-layout[data-mode="conversation-facts"] { display: block; }
  .chat-harness-layout[data-mode="conversation-facts"] .conversation-primary { width: 100%; height: 100%; }
  .chat-harness-layout[data-mode="conversation-facts"] .facts-dock { position: absolute; inset: 0 0 0 auto; width: min(var(--harness-dock-width), 92%); box-shadow: var(--sage-shadow-drawer); }
  .chat-harness-layout[data-mode="conversation-facts"] .facts-backdrop { position: absolute; z-index: 2; inset: 0; display: block; width: 100%; height: 100%; padding: 0; border: 0; background: var(--sage-overlay); }
  .chat-harness-layout[data-mode="conversation-facts"] .facts-closed-rail { position: absolute; z-index: 3; inset: 0 0 0 auto; width: 42px; }
}

@media (max-width: 767px) {
  .chat-harness-layout { display: block; padding-bottom: 48px; background: var(--sage-surface); }
  .harness-canvas,
  .workbench-dock { position: absolute; inset: 0 0 48px; width: 100%; height: auto; }
  .workbench-dock { display: grid; grid-template-rows: minmax(0, 1fr); border-left: 0; box-shadow: none; }
  .chat-harness-layout:not([data-mobile-pane="canvas"]) .harness-canvas,
  .chat-harness-layout[data-mobile-pane="canvas"] .workbench-dock,
  .dock-header,
  .dock-resize-handle,
  .dock-backdrop,
  .dock-rail { display: none; }
.mobile-surface-nav {
    position: absolute;
    z-index: 8;
    inset: auto 0 0;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    height: 48px;
    border-top: 1px solid var(--sage-border);
    background: var(--sage-surface);
  }
  .mobile-surface-nav button {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    min-width: 0;
    padding: 0;
    border: 0;
    color: var(--sage-text-muted);
    background: transparent;
    font-size: var(--sage-font-xs);
  }
  .mobile-surface-nav button[aria-pressed="true"] { color: var(--sage-success); }
  .chat-harness-layout[data-mode="conversation-facts"] { display: block; padding-bottom: 0; }
  .chat-harness-layout[data-mode="conversation-facts"] .conversation-primary { position: absolute; inset: 0; width: 100%; height: auto; }
  .chat-harness-layout[data-mode="conversation-facts"] .facts-dock { position: fixed; z-index: 45; inset: auto 0 calc(58px + env(safe-area-inset-bottom)); display: grid; width: 100%; height: min(72dvh, 620px); border-top: 1px solid var(--sage-border); border-left: 0; border-radius: var(--sage-radius-lg) var(--sage-radius-lg) 0 0; box-shadow: var(--sage-shadow-drawer); }
  .chat-harness-layout[data-mode="conversation-facts"] .facts-backdrop { position: fixed; z-index: 44; inset: 0 0 calc(58px + env(safe-area-inset-bottom)); display: block; width: 100%; height: auto; padding: 0; border: 0; background: var(--sage-overlay); }
  .chat-harness-layout[data-mode="conversation-facts"] .facts-closed-rail { display: none; }
  .chat-harness-layout[data-mode="conversation-facts"] .dock-resize-handle { display: none; }
}

.chat-harness-layout:not([data-has-details="true"]) .mobile-surface-nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }

@media (prefers-reduced-motion: reduce) {
  .workbench-dock { transition: none; }
}
</style>
