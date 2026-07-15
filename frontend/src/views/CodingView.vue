<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowDown, Eye, EyeOff, FileText, History, House, ScrollText, Settings, X } from 'lucide-vue-next'
import {
  CodingApprovalCard,
  CodingComposer,
  CodingDiffDrawer,
  CodingFilesDrawer,
  CodingGitBadge,
  CodingMessageTurn,
  CodingPlanApproval,
  CodingPlanPreview,
  CodingRunTrace,
  CodingSidebar,
  CodingThinkingIndicator,
} from '../components/coding'
import { useCodingStore } from '../stores/coding'
import { useMarkdown } from '../composables/useMarkdown'
import { useWorkbenchPreferences } from '../composables/useWorkbenchPreferences'
import { wireNaiveUI } from '../composables/useNaiveUI'

const store = useCodingStore()
const route = useRoute()
const router = useRouter()
const messagesRef = ref<HTMLElement | null>(null)
const showPlanPreview = ref(false)
const filesDrawerVisible = ref(false)
const followOutput = ref(true)
const returnToBottomVisible = ref(false)
const unseenMessageCount = ref(0)
const observedMessageCount = ref(0)
const leftOpen = ref(false)
const deepLinkError = ref('')
const leftToggleRef = ref<HTMLButtonElement | null>(null)
const leftSheetRef = ref<HTMLElement | null>(null)
const leftCloseRef = ref<HTMLButtonElement | null>(null)
const lastResolvedSessionId = ref<string | null>(null)
const viewGeneration = ref(0)
const { render } = useMarkdown()
const { showToolProcess } = useWorkbenchPreferences()
let scrollFrame: number | null = null

// Wire Naive UI message/dialog bridge for store-level notifications
wireNaiveUI()

const routeSessionId = computed(() => {
  const value = route.params.sessionId
  return typeof value === 'string' && value.trim() ? value : ''
})
const showThinkingIndicator = computed(() => {
  return Boolean(store.isThinking && store.thinkingPhase)
})
const thinkingCharacterState = computed(() => {
  if (store.pendingApproval) return 'waiting' as const
  const activeTools = store.turns.find((turn) => turn.run_id === store.activeRun?.run_id)?.tools ?? []
  if (activeTools.some((tool) => tool.status === 'running' || tool.status === 'blocked')) return 'tool' as const
  if (/失败|错误/.test(store.thinkingPhase)) return 'failed' as const
  return 'thinking' as const
})
const currentSession = computed(() => store.codingSessions.find((session) => session.session_id === store.sessionId))
const currentSessionTitle = computed(() => currentSession.value?.title || '未命名会话')
const currentRunStatus = computed(() => store.activeRun || store.isThinking ? '运行中' : '已就绪')
const mainInert = computed(() => leftOpen.value)
const drawerError = ref('')

function showDeepLinkError(error: unknown) {
  const detail = error instanceof Error ? error.message : String(error)
  deepLinkError.value = `无法打开会话：${detail}`
}

function clearDeepLinkError() {
  deepLinkError.value = ''
}

function cancelScrollFrame() {
  if (scrollFrame === null) return
  window.cancelAnimationFrame(scrollFrame)
  scrollFrame = null
}

function scrollToBottom(clearUnseen = true) {
  followOutput.value = true
  returnToBottomVisible.value = false
  cancelScrollFrame()
  nextTick(() => {
    scrollFrame = window.requestAnimationFrame(() => {
      scrollFrame = null
      if (!messagesRef.value) return
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
      if (clearUnseen) unseenMessageCount.value = 0
    })
  })
}

function scheduleFollowOutput() {
  if (!followOutput.value) {
    returnToBottomVisible.value = true
    return
  }
  scrollToBottom(false)
}

function findTimelineTurn(element: HTMLElement, turnId: string) {
  return [...element.querySelectorAll<HTMLElement>('[data-timeline-turn-id]')]
    .find((item) => item.dataset.timelineTurnId === turnId)
}

function restoreScrollAnchor() {
  nextTick(() => {
    const element = messagesRef.value
    const anchor = store.scrollAnchor
    if (!element || !anchor) return scrollToBottom()
    const target = findTimelineTurn(element, anchor.eventId)
    if (target) element.scrollTop = target.offsetTop + anchor.offset
    else scrollToBottom()
  })
}

function handleMessageScroll() {
  const element = messagesRef.value
  if (!element) return
  const { scrollHeight, scrollTop, clientHeight } = element
  const atBottom = scrollHeight - scrollTop - clientHeight < 80
  followOutput.value = atBottom
  returnToBottomVisible.value = !atBottom
  if (atBottom) unseenMessageCount.value = 0
  const containerTop = element.getBoundingClientRect().top
  const firstTurn = [...element.querySelectorAll<HTMLElement>('[data-timeline-turn-id]')]
    .find((item) => item.getBoundingClientRect().bottom >= containerTop)
  if (firstTurn?.dataset.timelineTurnId) {
    store.setScrollAnchor(firstTurn.dataset.timelineTurnId, scrollTop - firstTurn.offsetTop)
  }
}

function messagesForRun(runId: string) {
  return store.messages.filter((message) => message.run_id === runId)
}

function userMessagesForRun(runId: string) {
  return messagesForRun(runId).filter((message) => message.role === 'user')
}

function assistantMessagesForRun(runId: string) {
  return messagesForRun(runId).filter((message) => message.role === 'assistant')
}

function runHasAssistantReply(runId: string) {
  return assistantMessagesForRun(runId).some((message) => message.content.trim())
}

function isActiveTurn(runId: string) {
  return store.activeRun?.run_id === runId
}

function turnHasUser(runId: string) {
  return userMessagesForRun(runId).length > 0
}

function auditForRun(runId: string) {
  return store.runs.find((run) => run.run_id === runId)?.audit
}

function shouldShowRunTrace(runId: string, toolCount: number, approvalCount: number) {
  const hasEvidence = toolCount > 0 || approvalCount > 0 || Boolean(auditForRun(runId)?.steps.length)
  return hasEvidence && (showToolProcess.value || isActiveTurn(runId))
}

function pendingToolForRun(runId: string) {
  return isActiveTurn(runId) ? store.pendingApproval?.tool || '' : ''
}

const optimisticAttachedToTurn = computed(() => {
  return Boolean(store.optimisticMessage && store.activeRun &&
    store.turns.some((turn) => turn.run_id === store.activeRun?.run_id))
})

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

async function loadOlder() {
  const element = messagesRef.value
  const anchor = element?.querySelector<HTMLElement>('[data-timeline-turn-id]')
  const anchorId = anchor?.dataset.timelineTurnId
  const anchorOffset = anchor ? anchor.getBoundingClientRect().top - (element?.getBoundingClientRect().top ?? 0) : 0
  await store.loadOlderTimeline()
  await nextTick()
  if (element && anchorId) {
    const next = findTimelineTurn(element, anchorId)
    if (next) element.scrollTop += next.getBoundingClientRect().top - element.getBoundingClientRect().top - anchorOffset
  }
}

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
    await store.startNewSession()
    if (!store.sessionId) return
    rememberSession(store.sessionId)
    lastResolvedSessionId.value = store.sessionId
    await router.push(`/coding/session/${encodeURIComponent(store.sessionId)}`)
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
    await store.startNewSession()
    if (!store.sessionId) return
    rememberSession(store.sessionId)
    lastResolvedSessionId.value = store.sessionId
    await router.replace(`/coding/session/${encodeURIComponent(store.sessionId)}`)
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

watch(outputSignature, () => {
    const nextCount = store.messages.length
    if (followOutput.value) scheduleFollowOutput()
    else {
      returnToBottomVisible.value = true
      if (nextCount > observedMessageCount.value) {
        unseenMessageCount.value += nextCount - observedMessageCount.value
      }
    }
    observedMessageCount.value = nextCount
  }, { flush: 'post' })
watch(() => store.sessionId, () => {
  unseenMessageCount.value = 0
  followOutput.value = true
  returnToBottomVisible.value = false
  observedMessageCount.value = store.messages.length
  cancelScrollFrame()
})
watch(() => [store.sessionId, store.timelineInitialized], restoreScrollAnchor, { flush: 'post' })
watch(() => route.fullPath, () => { void synchronizeRoute() })

onMounted(async () => {
  await store.bootstrapModelCatalog()
  await synchronizeRoute()
  restoreScrollAnchor()
})

onBeforeUnmount(() => {
  viewGeneration.value += 1
  cancelScrollFrame()
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
        <section ref="messagesRef" class="message-area" aria-label="会话时间线" @scroll="handleMessageScroll">
          <button v-if="store.timelineHasMore" type="button" class="load-older-btn" :disabled="store.timelineLoading" @click="loadOlder">
            {{ store.timelineLoading ? '正在加载...' : '加载更早记录' }}
          </button>
          <div v-if="store.messages.length === 0" class="empty-state">
            <strong>Sage 已就绪</strong><span>输入编码任务，或使用 /review 开始审查。</span>
          </div>
          <CodingMessageTurn
            v-for="(msg, index) in store.legacyMessages"
            :key="`legacy:${index}:${msg.role}:${msg.content}`"
            :message="{ ...msg, id: `legacy:${index}` }"
            :rendered-content="render(msg.content)"
            :show-process="showToolProcess"
          />
          <template v-for="turn in store.turns" :key="turn.id">
            <div class="timeline-turn" :data-timeline-turn-id="turn.id">
              <CodingMessageTurn
                v-if="isActiveTurn(turn.run_id) && !turnHasUser(turn.run_id) && store.optimisticMessage"
                :message="store.optimisticMessage"
                :rendered-content="render(store.optimisticMessage.content)"
                :show-process="showToolProcess"
              />
              <CodingMessageTurn v-for="msg in userMessagesForRun(turn.run_id)" :key="msg.id" :message="msg" :rendered-content="render(msg.content)" :show-process="showToolProcess" />
              <div v-if="showThinkingIndicator && isActiveTurn(turn.run_id) && !runHasAssistantReply(turn.run_id)" class="active-run-status">
                <CodingThinkingIndicator :phase="store.thinkingPhase" :state="thinkingCharacterState" />
              </div>
              <CodingApprovalCard v-if="isActiveTurn(turn.run_id) && store.pendingApproval" :approval="store.pendingApproval" :busy="store.approvalBusy" @respond="store.respondApproval" />
              <CodingRunTrace
                v-if="shouldShowRunTrace(turn.run_id, turn.tools.length, turn.approvals.length)"
                :run-id="turn.run_id"
                :tools="turn.tools"
                :audit="auditForRun(turn.run_id)"
                :active="isActiveTurn(turn.run_id)"
                :pending-tool="pendingToolForRun(turn.run_id)"
              />
              <CodingMessageTurn
                v-for="msg in assistantMessagesForRun(turn.run_id)"
                :key="msg.id"
                :message="msg"
                :rendered-content="render(msg.content)"
                :show-process="false"
                :diff-file-count="store.diffInfoByRun[turn.run_id]?.file_count || 0"
                @view-diff="store.openRunDiff(turn.run_id)"
              />
            </div>
          </template>
          <CodingMessageTurn
            v-if="store.optimisticMessage && !optimisticAttachedToTurn"
            :message="store.optimisticMessage"
            :rendered-content="render(store.optimisticMessage.content)"
            :show-process="showToolProcess"
          />
          <CodingApprovalCard v-if="store.pendingApproval && !store.activeRun" :approval="store.pendingApproval" :busy="store.approvalBusy" @respond="store.respondApproval" />
          <div v-if="showThinkingIndicator && !store.activeRun" class="active-run-status">
            <CodingThinkingIndicator :phase="store.thinkingPhase" :state="thinkingCharacterState" />
          </div>
          <CodingPlanApproval v-if="store.planReview" />
          <p v-if="store.errorMessage || deepLinkError" class="error-text" role="alert">{{ store.errorMessage || deepLinkError }}</p>
          <button
            v-if="returnToBottomVisible"
            class="return-to-bottom"
            type="button"
            :aria-label="unseenMessageCount ? `回到底部，${unseenMessageCount} 条新消息` : '回到底部'"
            @click="scrollToBottom()"
          >
            <ArrowDown :size="15" />
            {{ unseenMessageCount ? `${unseenMessageCount} 条新消息` : '回到底部' }}
          </button>
          <span v-if="unseenMessageCount" class="sr-only" aria-live="polite">有 {{ unseenMessageCount }} 条新消息</span>
        </section>
        <CodingComposer />
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
.chat-shell { grid-row:2; position:relative; min-height:0; height:100%; overflow:hidden; }.pane-left,.pane-center { min-height:0; }.pane-left { position:absolute; z-index:32; inset:0 auto 0 0; width:min(340px,100%); overflow:hidden; border-right:1px solid var(--sage-border); background:var(--sage-surface); box-shadow:var(--sage-shadow-drawer); animation:session-drawer-in .18s ease-out; }.pane-center { position:relative; display:grid; grid-template-rows:56px minmax(0,1fr) auto; width:100%; height:100%; min-width:0; min-height:0; background:#fff; }.session-titlebar { display:flex; align-items:center; justify-content:space-between; gap:12px; min-width:0; padding:0 clamp(16px,4vw,52px); border-bottom:1px solid #edf0f3; }.session-title-copy { display:flex; align-items:center; gap:9px; min-width:0; }.session-title-copy strong { min-width:0; overflow:hidden; color:#283342; font-size:var(--sage-font-md); text-overflow:ellipsis; white-space:nowrap; }.session-title-copy span { display:inline-flex; align-items:center; flex:none; color:#748091; font-size:var(--sage-font-xs); }.session-title-copy span::before { width:6px; height:6px; margin-right:5px; border-radius:50%; background:#a7afb9; content:''; }.session-title-copy span.running { color:#137333; }.session-title-copy span.running::before { background:#16a34a; }.titlebar-actions { display:flex; align-items:center; justify-content:flex-end; gap:6px; min-width:0; }.files-toggle,.home-link { display:inline-grid; place-items:center; width:30px; height:30px; padding:0; border:1px solid transparent; border-radius:6px; color:#52606f; background:#fff; text-decoration:none; }.files-toggle:hover,.home-link:hover { border-color:#d8dee6; color:#1d4ed8; background:#f4f7fb; }
.message-area { min-height:0; overflow-y:auto; overscroll-behavior:contain; padding:22px clamp(18px,4vw,56px) 24px; scrollbar-gutter:stable; }.message-area > * { width:100%; max-width:var(--chat-content-max); margin-left:auto; margin-right:auto; }
.active-run-status { display:flex; width:100%; max-width:1120px; padding-left:42px; }
.active-run-status :deep(.thinking-indicator) { margin:0 0 12px; }
.load-older-btn { display:block; min-height:32px; margin:0 auto 18px; padding:0 12px; border:1px solid #d1d7df; border-radius:6px; color:#52606f; background:#fff; font-size:var(--sage-font-xs); }.load-older-btn:hover { background:#f5f7f9; }
.empty-state { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:5px; min-height:100%; color:#8a939e; text-align:center; }.empty-state strong { color:#4b5563; font-size:var(--sage-font-md); }.empty-state span { font-size:var(--sage-font-sm); }
.diff-btn { display:flex; align-items:center; justify-content:center; gap:6px; min-height:34px; margin-bottom:14px; border:1px solid #b9cbe6; border-radius:6px; color:#1d4ed8; background:#f5f8fd; font-size:var(--sage-font-xs); }.diff-btn:hover { background:#eaf1fb; }.error-text,.drawer-error { color:#b42318; font-size:var(--sage-font-xs); }.drawer-error { margin:48px 12px 0; }
.return-to-bottom { position:sticky; bottom:4px; display:flex; align-items:center; gap:6px; min-height:32px; margin-top:16px; padding:0 11px; border:1px solid var(--sage-border-strong); border-radius:var(--sage-radius); color:var(--sage-text); background:var(--sage-surface-raised); box-shadow:var(--sage-shadow); font-size:12px; font-weight:600; }.return-to-bottom:hover { background:var(--sage-surface-muted); }.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
.session-history-toggle { flex:none; }.session-backdrop { position:absolute; z-index:31; inset:0; display:block; background:rgba(20,28,38,.3); }.sheet-close { position:absolute; z-index:5; top:9px; right:9px; display:grid; place-items:center; width:30px; height:30px; padding:0; border:1px solid #d5dbe2; border-radius:6px; color:#52606f; background:#fff; }
@media (max-width:767px) { .message-area { padding:14px 12px 22px; }.pane-left { width:100%; }.session-titlebar { padding:0 10px 0 56px; gap:8px; }.session-title-copy { flex:1; }.session-title-copy span { display:none; }.titlebar-actions { flex:none; gap:3px; }.titlebar-actions :deep(.git-badge),.home-link,.process-toggle { display:none; } }
@keyframes session-drawer-in { from { opacity:0; transform:translateX(-18px); } }
@media (prefers-reduced-motion:reduce) { .pane-left { animation:none; } }

/* Visual-system overrides: compact workbench chrome stays neutral across light and dark themes. */
.sage-view { color:var(--sage-text); background:var(--sage-surface); }.header-icon,.files-toggle,.home-link { color:var(--sage-text-secondary); background:var(--sage-surface); }.header-icon:hover,.header-icon[aria-pressed="true"],.files-toggle:hover,.home-link:hover { border-color:var(--sage-border-strong); color:var(--sage-text); background:var(--sage-surface-muted); }.session-title-copy span.running::before { background:var(--sage-success); }.plan-banner { border-color:var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface-muted); }.plan-banner button,.load-older-btn { border-color:var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface); }.chat-shell,.pane-center { background:var(--sage-surface); }.pane-left,.session-titlebar { border-color:var(--sage-border); }.session-title-copy strong { color:var(--sage-text); }.session-title-copy span { color:var(--sage-text-muted); }.session-title-copy span::before { background:var(--sage-border-strong); }.empty-state { color:var(--sage-text-muted); }.empty-state strong { color:var(--sage-text-secondary); }.diff-btn { border-color:var(--sage-border-strong); color:var(--sage-text-secondary); background:var(--sage-surface-muted); }.diff-btn:hover,.load-older-btn:hover { background:var(--sage-surface-muted); }.error-text,.drawer-error { color:var(--sage-danger); }
@media (max-width:1179px) { .pane-left { border-color:var(--sage-border); box-shadow:var(--sage-shadow-drawer); }.session-backdrop { background:rgb(17 18 20 / 42%); }.sheet-close { border-color:var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface); } }

/* Hermes-inspired workbench proportions, implemented with Sage tokens and controls. */
.pane-center { grid-template-rows:48px minmax(0,1fr) auto; }
.session-titlebar { padding-right:clamp(18px,5vw,64px); padding-left:clamp(18px,5vw,64px); }
@media (max-width:767px) {
  .session-titlebar { padding-right:10px; padding-left:56px; }
}
@media (max-width:1100px) {
  .home-link,.titlebar-actions :deep(.git-badge) { display:none; }
}
</style>
