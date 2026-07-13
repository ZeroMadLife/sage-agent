<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowDown, Eye, EyeOff, FileText, GitCompareArrows, Menu, ScrollText, Settings, X } from 'lucide-vue-next'
import {
  CodingApprovalCard,
  CodingComposer,
  CodingDiffDrawer,
  CodingFilesDrawer,
  CodingGitBadge,
  CodingMessageTurn,
  CodingPlanApproval,
  CodingPlanPreview,
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
const isNearBottom = ref(true)
const unseenMessageCount = ref(0)
const observedMessageCount = ref(0)
const leftOpen = ref(false)
const isCompact = ref(window.innerWidth < 1180)
const deepLinkError = ref('')
const leftToggleRef = ref<HTMLButtonElement | null>(null)
const leftSheetRef = ref<HTMLElement | null>(null)
const leftCloseRef = ref<HTMLButtonElement | null>(null)
const lastResolvedSessionId = ref<string | null>(null)
const viewGeneration = ref(0)
const { render } = useMarkdown()
const { showToolProcess } = useWorkbenchPreferences()

// Wire Naive UI message/dialog bridge for store-level notifications
wireNaiveUI()

const routeSessionId = computed(() => {
  const value = route.params.sessionId
  return typeof value === 'string' && value.trim() ? value : ''
})
const showThinkingIndicator = computed(() => {
  return Boolean(store.isThinking && store.thinkingPhase)
})
const currentSession = computed(() => store.codingSessions.find((session) => session.session_id === store.sessionId))
const currentSessionTitle = computed(() => currentSession.value?.title || '未命名会话')
const currentRunStatus = computed(() => store.activeRun || store.isThinking ? '运行中' : '已就绪')
const mainInert = computed(() => isCompact.value && leftOpen.value)
const shellInert = computed(() => isCompact.value && leftOpen.value)
const drawerError = ref('')

function showDeepLinkError(error: unknown) {
  const detail = error instanceof Error ? error.message : String(error)
  deepLinkError.value = `无法打开会话：${detail}`
}

function clearDeepLinkError() {
  deepLinkError.value = ''
}

function scrollToBottom(clearUnseen = true) {
  nextTick(() => {
    if (!messagesRef.value) return
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    isNearBottom.value = true
    if (clearUnseen) unseenMessageCount.value = 0
  })
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
  isNearBottom.value = scrollHeight - scrollTop - clientHeight < 80
  if (isNearBottom.value) unseenMessageCount.value = 0
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

function processLabel(type: string) {
  return ({ context: '上下文', memory: '记忆', agent: '子智能体', approval: '审批', terminal: '运行终态' } as Record<string, string>)[type] ?? type
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
  if (isCompact.value) void nextTick(() => leftCloseRef.value?.focus())
}

function focusTrigger() {
  if (isCompact.value) void nextTick(() => leftToggleRef.value?.focus())
}

function focusableSheetElements(sheet: HTMLElement) {
  return [...sheet.querySelectorAll<HTMLElement>(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )].filter((element) => element.getAttribute('aria-hidden') !== 'true')
}

function trapSheetFocus(event: KeyboardEvent) {
  const sheet = leftSheetRef.value
  if (!isCompact.value || !leftOpen.value || event.key !== 'Tab' || !sheet) return
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

function updateBreakpoint() {
  isCompact.value = window.innerWidth < 1180
  if (!isCompact.value) leftOpen.value = false
}

watch(
  () => store.messages,
  () => {
    const nextCount = store.messages.length
    if (isNearBottom.value) scrollToBottom()
    else if (nextCount > observedMessageCount.value) unseenMessageCount.value += nextCount - observedMessageCount.value
    observedMessageCount.value = nextCount
  },
  { deep: true, flush: 'post' },
)
watch(() => store.sessionId, () => {
  unseenMessageCount.value = 0
  isNearBottom.value = true
  observedMessageCount.value = store.messages.length
})
watch(() => [store.sessionId, store.turns.map((turn) => turn.id).join('|')], restoreScrollAnchor, { flush: 'post' })
watch(() => route.fullPath, () => { void synchronizeRoute() })

onMounted(async () => {
  await synchronizeRoute()
  window.addEventListener('resize', updateBreakpoint)
  restoreScrollAnchor()
})

onBeforeUnmount(() => {
  viewGeneration.value += 1
  window.removeEventListener('resize', updateBreakpoint)
  // Runs are owned by the server; closing this view only detaches the observer transport.
  store.disconnect()
})
</script>

<template>
  <div class="sage-view">
    <header class="workbench-header" :class="{ 'is-inert': shellInert }" :inert="shellInert || undefined" :aria-hidden="shellInert ? 'true' : undefined">
      <button ref="leftToggleRef" class="header-icon left-toggle" type="button" title="打开会话" aria-label="打开会话" @click="openLeftSheet">
        <Menu :size="17" />
      </button>
      <div class="brand-block"><strong>Sage</strong><span>AI Coding Workbench</span></div>
      <div class="header-spacer"></div>
      <button class="header-icon process-toggle" type="button" :title="showToolProcess ? '隐藏工具过程' : '显示工具过程'" :aria-label="showToolProcess ? '隐藏工具过程' : '显示工具过程'" :aria-pressed="showToolProcess" @click="showToolProcess = !showToolProcess">
        <Eye v-if="showToolProcess" :size="16" /><EyeOff v-else :size="16" />
      </button>
      <button class="header-icon" type="button" title="打开设置" aria-label="打开设置" @click="openSettings"><Settings :size="17" /></button>
      <span class="connection-state"><i :class="{ connected: store.sessionId }"></i>{{ store.sessionId ? '已连接' : '连接中' }}</span>
    </header>

    <div v-if="store.runtimeMode === 'plan'" class="plan-banner">
      <ScrollText :size="15" /><strong>计划模式</strong><span>{{ store.planTopic }}</span>
      <button v-if="store.planPath" type="button" @click="showPlanPreview = true">查看计划</button>
    </div>

    <div class="chat-shell" :class="{ compact: isCompact }">
      <div v-if="isCompact && leftOpen" class="session-backdrop visible" aria-hidden="true" @click="closeLeftSheet"></div>
      <aside
        v-if="!isCompact || leftOpen"
        ref="leftSheetRef"
        class="pane-left"
        :class="{ open: leftOpen }"
        :role="isCompact ? 'dialog' : undefined"
        :aria-modal="isCompact ? 'true' : undefined"
        :aria-label="isCompact ? '会话列表' : undefined"
        @keydown.capture="handleLeftSheetKeydown"
      >
        <button ref="leftCloseRef" class="sheet-close" type="button" aria-label="关闭会话" title="关闭会话" @click="closeLeftSheet"><X :size="17" /></button>
        <p v-if="drawerError" class="drawer-error" role="alert">{{ drawerError }}</p>
        <CodingSidebar @navigate="navigateSession" @new-session="startNewSession" @archive-current="archiveCurrentSession" @settings="openSettings" @close="closeLeftSheet" />
      </aside>

      <main class="pane-center" :class="{ 'is-inert': mainInert }" :inert="mainInert || undefined" :aria-hidden="mainInert ? 'true' : undefined">
        <header class="session-titlebar">
          <div class="session-title-copy"><strong :title="currentSessionTitle">{{ currentSessionTitle }}</strong><span :class="{ running: store.activeRun || store.isThinking }">{{ currentRunStatus }}</span></div>
          <div class="titlebar-actions"><button class="files-toggle" type="button" aria-label="打开文件" title="打开文件" @click="openFilesDrawer"><FileText :size="16" /></button><CodingGitBadge /></div>
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
              <CodingMessageTurn v-for="msg in messagesForRun(turn.run_id)" :key="msg.id" :message="msg" :rendered-content="render(msg.content)" :show-process="showToolProcess" />
              <details v-if="showToolProcess && (turn.approvals.length || turn.context.length || turn.memory.length || turn.agents.length || turn.terminal)" class="process-details">
                <summary>运行过程</summary>
                <ul>
                  <li v-for="item in turn.approvals" :key="item.id">{{ processLabel('approval') }} · {{ item.tool }} · {{ item.status }}</li>
                  <li v-for="item in turn.context" :key="item.id">{{ processLabel('context') }} · {{ item.type }} · {{ item.status }}</li>
                  <li v-for="item in turn.memory" :key="item.id">{{ processLabel('memory') }} · {{ item.type }} · {{ item.status }}</li>
                  <li v-for="item in turn.agents" :key="item.id">{{ processLabel('agent') }} · {{ item.type }} · {{ item.status }}</li>
                  <li v-if="turn.terminal" :key="turn.terminal.event_id">{{ processLabel('terminal') }} · {{ turn.terminal.status }}</li>
                </ul>
              </details>
            </div>
          </template>
          <CodingApprovalCard v-if="store.pendingApproval" :approval="store.pendingApproval" :busy="store.approvalBusy" @respond="store.respondApproval" />
          <CodingThinkingIndicator v-if="showThinkingIndicator" :phase="store.thinkingPhase" />
          <button v-if="store.lastDiffInfo?.file_count" class="diff-btn" type="button" @click="store.openDiffDrawer"><GitCompareArrows :size="15" /> 查看变更（{{ store.lastDiffInfo.file_count }} 个文件）</button>
          <CodingPlanApproval v-if="store.planReview" />
          <p v-if="store.errorMessage || deepLinkError" class="error-text" role="alert">{{ store.errorMessage || deepLinkError }}</p>
          <button
            v-if="unseenMessageCount"
            class="return-to-bottom"
            type="button"
            :aria-label="`回到底部，${unseenMessageCount} 条新消息`"
            @click="scrollToBottom()"
          >
            <ArrowDown :size="15" />
            {{ unseenMessageCount }} 条新消息
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
.sage-view { --header-height:46px; display:grid; grid-template-rows:auto auto minmax(0,1fr); height:100dvh; color:var(--sage-text); background:var(--sage-surface); overflow:hidden; }
.workbench-header { display:flex; align-items:center; gap:9px; min-height:var(--header-height); padding:0 10px; border-bottom:1px solid #dfe3e8; background:#fff; }
.is-inert { pointer-events:none; }
.brand-block { display:flex; align-items:baseline; gap:7px; min-width:0; }.brand-block strong { color:#172033; font-size:16px; }.brand-block span { color:#7b8490; font-size:10px; white-space:nowrap; }
.header-spacer { flex:1; }.header-icon { display:grid; place-items:center; width:30px; height:30px; padding:0; border:1px solid transparent; border-radius:6px; color:#52606f; background:#fff; }.header-icon:hover,.header-icon[aria-pressed="true"] { border-color:#d8dee6; color:#1d4ed8; background:#f4f7fb; }.left-toggle { display:none; }
.connection-state { display:flex; align-items:center; gap:6px; color:#6b7280; font-size:10px; }.connection-state i { width:7px; height:7px; border-radius:50%; background:#a7afb9; }.connection-state i.connected { background:#16a34a; }
.plan-banner { display:flex; align-items:center; gap:8px; min-height:34px; padding:0 12px; border-bottom:1px solid #cddcf2; color:#244b82; background:#eff5fd; font-size:11px; }.plan-banner span { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.plan-banner button { margin-left:auto; min-height:24px; border:1px solid #adc5e7; border-radius:5px; color:#1d4ed8; background:#fff; font-size:10px; }
.chat-shell { display:grid; grid-template-columns:256px minmax(0,1fr); min-height:0; overflow:hidden; }.pane-left,.pane-center { min-height:0; }.pane-left { overflow:hidden; }.pane-center { position:relative; display:grid; grid-template-rows:56px minmax(0,1fr) auto; min-width:0; background:#fff; }.session-titlebar { display:flex; align-items:center; justify-content:space-between; gap:16px; min-width:0; padding:0 clamp(16px,4vw,52px); border-bottom:1px solid #edf0f3; }.session-title-copy { display:flex; align-items:center; gap:9px; min-width:0; }.session-title-copy strong { min-width:0; overflow:hidden; color:#283342; font-size:14px; text-overflow:ellipsis; white-space:nowrap; }.session-title-copy span { display:inline-flex; align-items:center; flex:none; color:#748091; font-size:11px; }.session-title-copy span::before { width:6px; height:6px; margin-right:5px; border-radius:50%; background:#a7afb9; content:''; }.session-title-copy span.running { color:#137333; }.session-title-copy span.running::before { background:#16a34a; }.titlebar-actions { display:flex; align-items:center; gap:8px; }.files-toggle { display:inline-grid; place-items:center; width:30px; height:30px; padding:0; border:1px solid transparent; border-radius:6px; color:#52606f; background:#fff; }.files-toggle:hover { border-color:#d8dee6; color:#1d4ed8; background:#f4f7fb; }
.message-area { min-height:0; overflow-y:auto; padding:18px clamp(16px,4vw,52px) 28px; scrollbar-gutter:stable; }.message-area > * { max-width:880px; margin-left:auto; margin-right:auto; }
.load-older-btn { display:block; min-height:30px; margin:0 auto 18px; padding:0 12px; border:1px solid #d1d7df; border-radius:6px; color:#52606f; background:#fff; font-size:11px; }.load-older-btn:hover { background:#f5f7f9; }
.empty-state { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:5px; min-height:48vh; color:#8a939e; text-align:center; }.empty-state strong { color:#4b5563; font-size:14px; }.empty-state span { font-size:12px; }
.process-details { margin:-9px 0 16px 40px; color:#687585; font-size:11px; }.process-details summary { cursor:pointer; font-weight:600; }.process-details ul { margin:7px 0 0; padding-left:18px; line-height:1.7; }
.diff-btn { display:flex; align-items:center; justify-content:center; gap:6px; min-height:32px; margin-bottom:14px; border:1px solid #b9cbe6; border-radius:6px; color:#1d4ed8; background:#f5f8fd; font-size:11px; }.diff-btn:hover { background:#eaf1fb; }.error-text,.drawer-error { color:#b42318; font-size:12px; }.drawer-error { margin:48px 12px 0; }
.return-to-bottom { position:sticky; bottom:4px; display:flex; align-items:center; gap:6px; min-height:32px; margin-top:16px; padding:0 11px; border:1px solid var(--sage-border-strong); border-radius:var(--sage-radius); color:var(--sage-text); background:var(--sage-surface-raised); box-shadow:var(--sage-shadow); font-size:12px; font-weight:600; }.return-to-bottom:hover { background:var(--sage-surface-muted); }.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
.sheet-close,.session-backdrop { display:none; }
@media (max-width:1179px) { .left-toggle { display:grid; }.chat-shell { display:block; }.pane-center { height:100%; }.pane-left { position:fixed; z-index:32; top:var(--header-height); bottom:0; left:0; width:min(320px,100vw); border-right:1px solid #dfe3e8; box-shadow:12px 0 36px rgba(30,41,59,.16); transform:translateX(-101%); transition:transform .18s ease; }.pane-left.open { transform:translateX(0); }.session-backdrop { position:fixed; z-index:31; inset:var(--header-height) 0 0; display:block; background:rgba(20,28,38,.3); opacity:0; pointer-events:none; }.session-backdrop.visible { opacity:1; pointer-events:auto; }.sheet-close { position:absolute; z-index:5; top:9px; right:9px; display:grid; place-items:center; width:30px; height:30px; padding:0; border:1px solid #d5dbe2; border-radius:6px; color:#52606f; background:#fff; } }
@media (max-width:767px) { .brand-block span,.connection-state { display:none; }.message-area { padding:14px 12px 22px; }.pane-left { top:0; width:100%; }.session-backdrop { inset:0; }.session-titlebar { padding:0 14px; }.process-details { margin-left:34px; } }
@media (prefers-reduced-motion:reduce) { .pane-left { transition:none; } }

/* Visual-system overrides: compact workbench chrome stays neutral across light and dark themes. */
.sage-view { color:var(--sage-text); background:var(--sage-surface); }.workbench-header { border-color:var(--sage-border); background:var(--sage-surface); }.brand-block strong { color:var(--sage-text); }.brand-block span,.connection-state { color:var(--sage-text-muted); }.header-icon,.files-toggle { color:var(--sage-text-secondary); background:var(--sage-surface); }.header-icon:hover,.header-icon[aria-pressed="true"],.files-toggle:hover { border-color:var(--sage-border-strong); color:var(--sage-text); background:var(--sage-surface-muted); }.connection-state i { background:var(--sage-border-strong); }.connection-state i.connected,.session-title-copy span.running::before { background:var(--sage-success); }.plan-banner { border-color:var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface-muted); }.plan-banner button,.load-older-btn { border-color:var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface); }.chat-shell,.pane-center { background:var(--sage-surface); }.pane-left,.session-titlebar { border-color:var(--sage-border); }.session-title-copy strong { color:var(--sage-text); }.session-title-copy span { color:var(--sage-text-muted); }.session-title-copy span::before { background:var(--sage-border-strong); }.empty-state { color:var(--sage-text-muted); }.empty-state strong { color:var(--sage-text-secondary); }.process-details { color:var(--sage-text-muted); }.diff-btn { border-color:var(--sage-border-strong); color:var(--sage-text-secondary); background:var(--sage-surface-muted); }.diff-btn:hover,.load-older-btn:hover { background:var(--sage-surface-muted); }.error-text,.drawer-error { color:var(--sage-danger); }
@media (max-width:1179px) { .pane-left { border-color:var(--sage-border); box-shadow:var(--sage-shadow-drawer); }.session-backdrop { background:rgb(17 18 20 / 42%); }.sheet-close { border-color:var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface); } }
</style>
