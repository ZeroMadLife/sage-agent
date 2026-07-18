<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Eye, EyeOff, FileText, History, House, ScrollText, Settings, X } from 'lucide-vue-next'
import {
  CodingComposer,
  CodingApprovalCard,
  CodingDiffDrawer,
  CodingFilesDrawer,
  CodingGitBadge,
  CodingHarnessWorkbench,
  CodingPlanApproval,
  CodingPlanPreview,
  CodingSidebar,
} from '../components/coding'
import { ChatConversation, ChatDock, ChatHarnessLayout } from '../components/harness'
import { projectLatestCodingHarness } from '../harness/surfaces/coding'
import { projectCodingReviewBundle } from '../harness/surfaces/codingReviewBundle'
import type { HarnessSurfaceContext } from '../harness/types'
import { useCodingStore } from '../stores/coding'
import { useWorkbenchPreferences } from '../composables/useWorkbenchPreferences'
import { wireNaiveUI } from '../composables/useNaiveUI'

const store = useCodingStore()
const route = useRoute()
const router = useRouter()
const showPlanPreview = ref(false)
const filesDrawerVisible = ref(false)
const leftOpen = ref(false)
const selectedHarnessRunId = ref('')
const deepLinkError = ref('')
const leftToggleRef = ref<HTMLButtonElement | null>(null)
const leftSheetRef = ref<HTMLElement | null>(null)
const leftCloseRef = ref<HTMLButtonElement | null>(null)
const lastResolvedSessionId = ref<string | null>(null)
const viewGeneration = ref(0)
const { showToolProcess } = useWorkbenchPreferences()

// Wire Naive UI message/dialog bridge for store-level notifications
wireNaiveUI()

const routeSessionId = computed(() => {
  const value = route.params.sessionId
  return typeof value === 'string' && value.trim() ? value : ''
})
const currentSession = computed(() => store.codingSessions.find((session) => session.session_id === store.sessionId))
const currentSessionTitle = computed(() => currentSession.value?.title || '未命名会话')
const currentRunStatus = computed(() => store.activeRun || store.isThinking ? '运行中' : '已就绪')
const harnessProjection = computed(() => projectLatestCodingHarness(
  store.visibleTimeline,
  selectedHarnessRunId.value || store.activeRun?.run_id || '',
))
const harnessToolCallCount = computed(() => store.visibleTimeline.filter((event) => (
  event.run_id === harnessProjection.value.runId
  && event.kind === 'tool'
  && event.payload.type === 'tool_call'
)).length)
const harnessReviewBundle = computed(() => {
  const runId = harnessProjection.value.runId
  return projectCodingReviewBundle({
    runId,
    turn: store.turns.find((turn) => turn.run_id === runId) ?? null,
    run: store.runs.find((run) => run.run_id === runId) ?? null,
    diff: store.diffInfoByRun[runId] ?? null,
    memoryProposals: store.memoryProposals,
  })
})
const harnessDepositBusy = computed(() => Boolean(
  harnessReviewBundle.value.deposit.proposalId
  && store.memoryProposalBusy[harnessReviewBundle.value.deposit.proposalId],
))
const codingContext = computed<HarnessSurfaceContext | null>(() => {
  if (!store.workspaceId) return null
  const workspaceLabel = store.workspaceRoot.split('/').filter(Boolean).at(-1) || 'coding'
  return {
    surface: 'coding',
    workspaceId: store.workspaceId,
    resource: {
      type: 'coding_workspace',
      id: store.workspaceId,
      label: workspaceLabel,
    },
    selection: store.previewPath ? {
      type: 'coding_file',
      id: store.previewPath,
      label: store.previewPath,
    } : null,
    operationRefs: [],
  }
})
const mainInert = computed(() => leftOpen.value)
const drawerError = ref('')

function showDeepLinkError(error: unknown) {
  const detail = error instanceof Error ? error.message : String(error)
  deepLinkError.value = `无法打开会话：${detail}`
}

function clearDeepLinkError() {
  deepLinkError.value = ''
}

watch(() => store.activeRun?.run_id || '', (runId, previousRunId) => {
  if (runId && runId !== previousRunId) selectedHarnessRunId.value = runId
})
watch(() => store.sessionId, () => { selectedHarnessRunId.value = '' })

function selectHarnessRun(runId: string) {
  if (runId) selectedHarnessRunId.value = runId
}

const outputSignature = computed(() => JSON.stringify({
  session: store.sessionId,
  thinking: store.isThinking,
  phase: store.thinkingPhase,
  approval: store.pendingApproval?.approval_id ?? '',
  messages: store.messages.map((message) => ({
    id: message.id,
    run: message.run_id,
    role: message.role,
    content: message.content,
    thinking: message.isThinking,
    tools: message.tools?.map((tool) => [tool.tool, tool.status, tool.content.length]),
  })),
  optimistic: store.optimisticMessage?.id ?? '',
}))

function rememberSession(sessionId: string) {
  if (sessionId) localStorage.setItem('sage.coding.recentSessionId', sessionId)
}

function mostRecentActiveSessionId() {
  return store.codingSessions
    .filter((session) => !session.archived)
    .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))[0]?.session_id || ''
}

async function selectAndRoute(sessionId: string, replace = false) {
  await store.selectSession(sessionId)
  rememberSession(sessionId)
  lastResolvedSessionId.value = sessionId
  if (replace) await router.replace(`/coding/session/${encodeURIComponent(sessionId)}`)
  else await router.push(`/coding/session/${encodeURIComponent(sessionId)}`)
}

function showDrawerError(error: unknown, action: string) {
  const detail = error instanceof Error ? error.message : String(error)
  drawerError.value = `无法${action}：${detail}`
}

async function synchronizeRoute() {
  if (!route.path.startsWith('/coding')) return
  const generation = ++viewGeneration.value
  const targetSessionId = routeSessionId.value
  if (targetSessionId && targetSessionId === lastResolvedSessionId.value) return
  try {
    if (!targetSessionId) {
      if (store.sessionId) {
        await store.restoreCurrentSession()
        if (generation !== viewGeneration.value) return
        if (store.sessionId) {
          lastResolvedSessionId.value = store.sessionId
          rememberSession(store.sessionId)
          await router.replace(`/coding/session/${encodeURIComponent(store.sessionId)}`)
        }
      } else {
        const recentSessionId = localStorage.getItem('sage.coding.recentSessionId') || ''
        if (recentSessionId) {
          try {
            await selectAndRoute(recentSessionId, true)
            if (generation !== viewGeneration.value) return
            lastResolvedSessionId.value = recentSessionId
            clearDeepLinkError()
            return
          } catch {
            localStorage.removeItem('sage.coding.recentSessionId')
          }
        }
        await store.loadSessions()
        if (generation !== viewGeneration.value) return
        const fallbackSessionId = mostRecentActiveSessionId()
        if (fallbackSessionId) {
          await selectAndRoute(fallbackSessionId, true)
          if (generation !== viewGeneration.value) return
          lastResolvedSessionId.value = fallbackSessionId
          clearDeepLinkError()
          return
        }
        await store.initialize()
        if (generation !== viewGeneration.value) return
        if (store.sessionId) {
          lastResolvedSessionId.value = store.sessionId
          rememberSession(store.sessionId)
          await router.replace(`/coding/session/${encodeURIComponent(store.sessionId)}`)
        }
      }
    } else if (targetSessionId === store.sessionId) {
      await store.restoreCurrentSession()
      if (generation !== viewGeneration.value) return
      lastResolvedSessionId.value = targetSessionId
      rememberSession(targetSessionId)
    } else {
      await store.selectSession(targetSessionId)
      if (generation !== viewGeneration.value) return
      lastResolvedSessionId.value = targetSessionId
      rememberSession(targetSessionId)
    }
    clearDeepLinkError()
  } catch (error) {
    showDeepLinkError(error)
  }
}

async function navigateSession(sessionId: string) {
  clearDeepLinkError()
  drawerError.value = ''
  try {
    await selectAndRoute(sessionId)
    closeLeftSheet()
  } catch (error) {
    showDrawerError(error, '切换会话')
  }
}

async function startNewSession() {
  clearDeepLinkError()
  drawerError.value = ''
  try {
    const sessionId = await store.startNewSession()
    if (!sessionId) return
    rememberSession(sessionId)
    lastResolvedSessionId.value = sessionId
    await router.push(`/coding/session/${encodeURIComponent(sessionId)}`)
    closeLeftSheet()
  } catch (error) {
    if (leftOpen.value) showDrawerError(error, '新建会话')
    else showDeepLinkError(error)
  }
}

async function archiveCurrentSession(sessionId: string) {
  clearDeepLinkError()
  drawerError.value = ''
  try {
    await store.setSessionArchived(sessionId, true)
    const replacementSessionId = await store.startNewSession()
    if (!replacementSessionId) return
    rememberSession(replacementSessionId)
    lastResolvedSessionId.value = replacementSessionId
    await router.replace(`/coding/session/${encodeURIComponent(replacementSessionId)}`)
    closeLeftSheet()
  } catch (error) {
    if (leftOpen.value) showDrawerError(error, '归档会话')
    else showDeepLinkError(error)
  }
}

function openSettings() {
  void router.push('/settings/appearance')
}

function openFilesDrawer() {
  filesDrawerVisible.value = true
  void store.loadFiles('.')
}

function focusSheetClose() {
  void nextTick(() => leftCloseRef.value?.focus())
}

function focusTrigger() {
  void nextTick(() => leftToggleRef.value?.focus())
}

function focusableSheetElements(sheet: HTMLElement) {
  return [...sheet.querySelectorAll<HTMLElement>(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )].filter((element) => element.getAttribute('aria-hidden') !== 'true')
}

function trapSheetFocus(event: KeyboardEvent) {
  const sheet = leftSheetRef.value
  if (!leftOpen.value || event.key !== 'Tab' || !sheet) return
  const focusable = focusableSheetElements(sheet)
  if (focusable.length === 0) {
    event.preventDefault()
    sheet.focus()
    return
  }
  const currentIndex = focusable.indexOf(document.activeElement as HTMLElement)
  const nextIndex = event.shiftKey ? currentIndex - 1 : currentIndex + 1
  if ((!event.shiftKey && (currentIndex === -1 || nextIndex >= focusable.length)) || (event.shiftKey && currentIndex <= 0)) {
    event.preventDefault()
    focusable[event.shiftKey ? focusable.length - 1 : 0].focus()
  }
}

function openLeftSheet() {
  drawerError.value = ''
  leftOpen.value = true
  focusSheetClose()
}

function closeLeftSheet() {
  drawerError.value = ''
  leftOpen.value = false
  focusTrigger()
}

function handleLeftSheetKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeLeftSheet()
    return
  }
  trapSheetFocus(event)
}

watch(() => route.fullPath, () => { void synchronizeRoute() })

onMounted(async () => {
  await store.bootstrapModelCatalog()
  await synchronizeRoute()
})

onBeforeUnmount(() => {
  viewGeneration.value += 1
  // Runs are owned by the server; closing this view only detaches the observer transport.
  store.disconnect()
})
</script>

<template>
  <div class="sage-view">
    <div v-if="store.runtimeMode === 'plan'" class="plan-banner">
      <ScrollText :size="15" /><strong>计划模式</strong><span>{{ store.planTopic }}</span>
      <button v-if="store.planPath" type="button" @click="showPlanPreview = true">查看计划</button>
    </div>

    <div class="chat-shell">
      <div v-if="leftOpen" class="session-backdrop visible" aria-hidden="true" @click="closeLeftSheet"></div>
      <aside
        v-if="leftOpen"
        ref="leftSheetRef"
        class="pane-left"
        role="dialog"
        aria-modal="true"
        aria-label="会话列表"
        @keydown.capture="handleLeftSheetKeydown"
      >
        <button ref="leftCloseRef" class="sheet-close" type="button" aria-label="关闭会话" title="关闭会话" @click="closeLeftSheet"><X :size="17" /></button>
        <p v-if="drawerError" class="drawer-error" role="alert">{{ drawerError }}</p>
        <CodingSidebar @navigate="navigateSession" @new-session="startNewSession" @archive-current="archiveCurrentSession" @settings="openSettings" @close="closeLeftSheet" />
      </aside>

      <main class="pane-center" :class="{ 'is-inert': mainInert }" :inert="mainInert || undefined" :aria-hidden="mainInert ? 'true' : undefined">
        <ChatHarnessLayout class="coding-harness-layout" surface-label="Coding" chat-label="主对话" :show-details="false">
          <template #canvas>
            <section class="harness-workbench-pane">
              <header class="session-titlebar">
                <div class="session-title-copy"><strong :title="currentSessionTitle">{{ currentSessionTitle }}</strong><span :class="{ running: store.activeRun || store.isThinking }">{{ currentRunStatus }}</span></div>
                <div class="titlebar-actions">
                  <button ref="leftToggleRef" class="header-icon session-history-toggle" type="button" title="打开会话历史" aria-label="打开会话" @click="openLeftSheet"><History :size="16" /></button>
                  <button class="header-icon process-toggle" type="button" :title="showToolProcess ? '隐藏运行摘要' : '显示运行摘要'" :aria-label="showToolProcess ? '隐藏运行摘要' : '显示运行摘要'" :aria-pressed="showToolProcess" @click="showToolProcess = !showToolProcess"><Eye v-if="showToolProcess" :size="16" /><EyeOff v-else :size="16" /></button>
                  <RouterLink class="home-link" to="/assistant" aria-label="返回今天" title="返回今天"><House :size="16" /></RouterLink>
                  <button class="files-toggle" type="button" aria-label="打开文件" title="打开文件" @click="openFilesDrawer"><FileText :size="16" /></button>
                  <CodingGitBadge />
                  <button class="header-icon" type="button" title="打开设置" aria-label="打开设置" @click="openSettings"><Settings :size="16" /></button>
                </div>
              </header>
              <CodingHarnessWorkbench
                :projection="harnessProjection"
                :session-title="currentSessionTitle"
                :tool-call-count="harnessToolCallCount"
                :review-bundle="harnessReviewBundle"
                :deposit-busy="harnessDepositBusy"
                @approve-deposit="store.approveMemoryProposal"
                @reject-deposit="store.rejectMemoryProposal"
              />
            </section>
          </template>

          <template #chat>
            <ChatDock
              :projection="harnessProjection"
              :connection-state="store.connectionState"
              :context="codingContext"
              :output-signature="outputSignature"
              :message-count="store.messages.length"
              :session-key="store.sessionId"
              :timeline-ready="store.timelineInitialized"
              :scroll-anchor="store.scrollAnchor"
              :has-more="store.timelineHasMore"
              :loading="store.timelineLoading"
              :is-empty="store.messages.length === 0"
              :load-older="store.loadOlderTimeline"
              empty-description="输入学习目标、实践任务，或使用 /review 开始审查。"
              @anchor-change="store.setScrollAnchor"
            >
              <template v-if="store.pendingApproval" #attention>
                <CodingApprovalCard
                  :approval="store.pendingApproval"
                  :busy="store.approvalBusy"
                  compact
                  @respond="store.respondApproval"
                />
              </template>
              <ChatConversation
                :legacy-messages="store.legacyMessages"
                :messages="store.messages"
                :turns="store.turns"
                :optimistic-message="store.optimisticMessage"
                :active-run-id="store.activeRun?.run_id || ''"
                :selected-run-id="harnessProjection.runId"
                :pending-approval="store.pendingApproval"
                :show-approval-card="false"
                :approval-busy="store.approvalBusy"
                :is-thinking="store.isThinking"
                :thinking-phase="store.thinkingPhase"
                :show-process="showToolProcess"
                :runs="store.runs"
                :diff-info-by-run="store.diffInfoByRun"
                :error-message="store.errorMessage || deepLinkError"
                @select-run="selectHarnessRun"
                @respond-approval="store.respondApproval"
                @open-run-diff="store.openRunDiff"
              />
              <template #composer>
                <CodingPlanApproval v-if="store.planReview" />
                <CodingComposer density="compact" :surface-context="codingContext" />
              </template>
            </ChatDock>
          </template>
        </ChatHarnessLayout>
      </main>
    </div>

    <CodingPlanPreview v-if="showPlanPreview" :plan-path="store.planPath" :topic="store.planTopic" :visible="true" @close="showPlanPreview = false" />
    <CodingFilesDrawer :visible="filesDrawerVisible" @close="filesDrawerVisible = false" />
    <CodingDiffDrawer :diff="store.currentDiffData" :visible="store.diffDrawerVisible" @close="store.closeDiffDrawer" />
  </div>
</template>

<style scoped>
.sage-view { --chat-content-max:1120px; position:relative; display:grid; grid-template-rows:auto minmax(0,1fr); width:100%; height:100dvh; min-width:0; min-height:0; color:var(--sage-text); background:var(--sage-surface); overflow:hidden; }
.is-inert { pointer-events:none; }
.header-icon { display:grid; place-items:center; width:30px; height:30px; padding:0; border:1px solid transparent; border-radius:6px; color:#52606f; background:#fff; }.header-icon:hover,.header-icon[aria-pressed="true"] { border-color:#d8dee6; color:#1d4ed8; background:#f4f7fb; }
.plan-banner { grid-row:1; display:flex; align-items:center; gap:8px; min-height:34px; padding:0 12px; border-bottom:1px solid #cddcf2; color:#244b82; background:#eff5fd; font-size:var(--sage-font-sm); }.plan-banner span { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.plan-banner button { margin-left:auto; min-height:24px; border:1px solid #adc5e7; border-radius:5px; color:#1d4ed8; background:#fff; font-size:var(--sage-font-xs); }
.chat-shell { grid-row:2; position:relative; min-height:0; height:100%; overflow:hidden; }.pane-left,.pane-center { min-height:0; }.pane-left { position:absolute; z-index:32; inset:0 auto 0 0; width:min(340px,100%); overflow:hidden; border-right:1px solid var(--sage-border); background:var(--sage-surface); box-shadow:var(--sage-shadow-drawer); animation:session-drawer-in .18s ease-out; }.pane-center { position:relative; width:100%; height:100%; min-width:0; min-height:0; background:var(--sage-surface); }.coding-harness-layout { width:100%; height:100%; }.harness-workbench-pane { container:workbench-pane / inline-size; display:grid; grid-template-rows:48px minmax(0,1fr); width:100%; height:100%; min-width:0; min-height:0; }.coding-chat-pane { --chat-content-max:100%; display:grid; grid-template-rows:minmax(0,1fr) auto; width:100%; height:100%; min-width:0; min-height:0; background:var(--sage-surface); }.session-titlebar { display:flex; align-items:center; justify-content:space-between; gap:10px; min-width:0; padding:0 18px; border-bottom:1px solid #edf0f3; }.session-title-copy { display:flex; align-items:center; gap:9px; min-width:0; }.session-title-copy strong { min-width:0; overflow:hidden; color:#283342; font-size:var(--sage-font-md); text-overflow:ellipsis; white-space:nowrap; }.session-title-copy span { display:inline-flex; align-items:center; flex:none; color:#748091; font-size:var(--sage-font-xs); }.session-title-copy span::before { width:6px; height:6px; margin-right:5px; border-radius:50%; background:#a7afb9; content:''; }.session-title-copy span.running { color:#137333; }.session-title-copy span.running::before { background:#16a34a; }.titlebar-actions { display:flex; align-items:center; justify-content:flex-end; gap:4px; min-width:0; }.files-toggle,.home-link { display:inline-grid; place-items:center; width:30px; height:30px; padding:0; border:1px solid transparent; border-radius:6px; color:#52606f; background:#fff; text-decoration:none; }.files-toggle:hover,.home-link:hover { border-color:#d8dee6; color:#1d4ed8; background:#f4f7fb; }
.coding-chat-pane .message-area { padding:14px 12px 16px; }
.coding-chat-pane :deep(.composer) { padding-right:12px; padding-left:12px; }
.coding-chat-pane :deep(.message-turn) { gap:9px; margin-bottom:18px; }
.coding-chat-pane :deep(.message-avatar) { width:26px; height:26px; }
.coding-chat-pane :deep(.message-author) { margin-bottom:6px; }
.coding-chat-pane :deep(.message-content-shell) { font-size:14px; line-height:1.68; }
.coding-chat-pane :deep(.message-turn.user .message-content-shell) { padding:8px 10px; }
.coding-chat-pane :deep(.run-trace) { margin-bottom:10px; }
.coding-chat-pane :deep(.run-trace summary) { min-height:38px; }
.coding-chat-pane :deep(.thinking-indicator) { margin-bottom:9px; }
.coding-chat-pane :deep(.thinking-indicator .sage-character) { width:50px; height:50px; }
.timeline-turn { position:relative; margin-left:-6px; padding-left:6px; border-left:2px solid transparent; }
.timeline-turn.is-harness-selected { border-left-color:color-mix(in srgb,var(--sage-success) 68%,transparent); }
.active-run-status { display:flex; width:100%; max-width:1120px; padding-left:42px; }
.active-run-status :deep(.thinking-indicator) { margin:0 0 12px; }
.empty-state { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:5px; min-height:100%; color:#8a939e; text-align:center; }.empty-state strong { color:#4b5563; font-size:var(--sage-font-md); }.empty-state span { font-size:var(--sage-font-sm); }
.diff-btn { display:flex; align-items:center; justify-content:center; gap:6px; min-height:34px; margin-bottom:14px; border:1px solid #b9cbe6; border-radius:6px; color:#1d4ed8; background:#f5f8fd; font-size:var(--sage-font-xs); }.diff-btn:hover { background:#eaf1fb; }.error-text,.drawer-error { color:#b42318; font-size:var(--sage-font-xs); }.drawer-error { margin:48px 12px 0; }
.session-history-toggle { flex:none; }.session-backdrop { position:absolute; z-index:31; inset:0; display:block; background:rgba(20,28,38,.3); }.sheet-close { position:absolute; z-index:5; top:9px; right:9px; display:grid; place-items:center; width:30px; height:30px; padding:0; border:1px solid #d5dbe2; border-radius:6px; color:#52606f; background:#fff; }
@media (max-width:767px) { .pane-left { width:100%; }.session-titlebar { padding:0 10px 0 56px; gap:8px; }.session-title-copy { flex:1; }.session-title-copy span { display:none; }.titlebar-actions { flex:none; gap:3px; }.titlebar-actions :deep(.git-badge),.home-link,.process-toggle { display:none; } }
@keyframes session-drawer-in { from { opacity:0; transform:translateX(-18px); } }
@media (prefers-reduced-motion:reduce) { .pane-left { animation:none; } }

/* Visual-system overrides: compact workbench chrome stays neutral across light and dark themes. */
.sage-view { color:var(--sage-text); background:var(--sage-surface); }.header-icon,.files-toggle,.home-link { color:var(--sage-text-secondary); background:var(--sage-surface); }.header-icon:hover,.header-icon[aria-pressed="true"],.files-toggle:hover,.home-link:hover { border-color:var(--sage-border-strong); color:var(--sage-text); background:var(--sage-surface-muted); }.session-title-copy span.running::before { background:var(--sage-success); }.plan-banner { border-color:var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface-muted); }.plan-banner button { border-color:var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface); }.chat-shell,.pane-center { background:var(--sage-surface); }.pane-left,.session-titlebar { border-color:var(--sage-border); }.session-title-copy strong { color:var(--sage-text); }.session-title-copy span { color:var(--sage-text-muted); }.session-title-copy span::before { background:var(--sage-border-strong); }.empty-state { color:var(--sage-text-muted); }.empty-state strong { color:var(--sage-text-secondary); }.diff-btn { border-color:var(--sage-border-strong); color:var(--sage-text-secondary); background:var(--sage-surface-muted); }.diff-btn:hover { background:var(--sage-surface-muted); }.error-text,.drawer-error { color:var(--sage-danger); }
@media (max-width:1179px) { .pane-left { border-color:var(--sage-border); box-shadow:var(--sage-shadow-drawer); }.session-backdrop { background:rgb(17 18 20 / 42%); }.sheet-close { border-color:var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface); } }

/* The center canvas owns its density; controls yield as the dock narrows it. */
@container workbench-pane (max-width:880px) {
  .titlebar-actions :deep(.git-badge) { display:none; }
}
@container workbench-pane (max-width:720px) {
  .home-link,.files-toggle { display:none; }
  .session-title-copy span { display:none; }
}
@media (max-width:767px) {
  .session-titlebar { padding-right:10px; padding-left:56px; }
}
@media (max-width:1100px) {
  .home-link,.titlebar-actions :deep(.git-badge) { display:none; }
}
</style>
