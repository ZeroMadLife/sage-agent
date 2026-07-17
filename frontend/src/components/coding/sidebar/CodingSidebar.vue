<script setup lang="ts">
import { Archive, ArchiveRestore, ChevronDown, ChevronRight, FolderGit2, Pencil, Pin, PinOff, Plus, Search, Settings } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { useCodingStore } from '../../../stores/coding'

const store = useCodingStore()
const emit = defineEmits<{
  navigate: [sessionId: string]
  newSession: []
  archiveCurrent: [sessionId: string]
  close: []
  settings: []
}>()
const SESSION_LIMIT = 18
const query = ref('')
const showAll = ref(false)
const showArchived = ref(false)
const renamingSessionId = ref<string | null>(null)
const renameDraft = ref('')
const sidebarError = ref('')

const matchingSessions = computed(() => {
  const normalized = query.value.trim().toLowerCase()
  if (!normalized) return store.codingSessions
  return store.codingSessions.filter((session) =>
    `${session.title} ${session.session_id}`.toLowerCase().includes(normalized),
  )
})

const filteredSessions = computed(() => matchingSessions.value.filter((session) => !session.archived))
const archivedSessions = computed(() => matchingSessions.value.filter((session) => session.archived))

const visibleSessions = computed(() => showAll.value
  ? filteredSessions.value
  : filteredSessions.value.slice(0, SESSION_LIMIT))

watch(query, () => { showAll.value = false })

function selectSession(sessionId: string) {
  sidebarError.value = ''
  emit('navigate', sessionId)
}

function startSession() {
  sidebarError.value = ''
  emit('newSession')
}

function beginRename(sessionId: string, title: string) {
  sidebarError.value = ''
  renamingSessionId.value = sessionId
  renameDraft.value = title
}

async function commitRename(sessionId: string) {
  const title = renameDraft.value.trim()
  sidebarError.value = ''
  if (!title) {
    renamingSessionId.value = null
    return
  }
  try {
    await store.renameSession(sessionId, title)
    renamingSessionId.value = null
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    sidebarError.value = `无法重命名会话：${detail}`
  }
}

async function setPinned(sessionId: string, pinned: boolean) {
  sidebarError.value = ''
  try {
    await store.setSessionPinned(sessionId, pinned)
    await store.loadSessions()
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    sidebarError.value = `无法${pinned ? '置顶' : '取消置顶'}会话：${detail}`
  }
}

async function setArchived(sessionId: string, archived: boolean) {
  sidebarError.value = ''
  if (archived && store.sessionId === sessionId) {
    emit('archiveCurrent', sessionId)
    return
  }
  try {
    await store.setSessionArchived(sessionId, archived)
    await store.loadSessions()
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    sidebarError.value = `无法${archived ? '归档' : '恢复'}会话：${detail}`
  }
}

function sessionState(sessionId: string) {
  const state = store.sessionsById[sessionId]
  if (state?.activeRun || state?.isThinking) return 'running'
  if (state?.errorMessage) return 'error'
  return 'idle'
}

function modeLabel(mode: string) {
  return mode === 'plan' ? '计划' : '编码'
}
</script>

<template>
  <aside class="session-sidebar" aria-label="工作区与会话">
    <header class="workspace-heading">
      <div class="workspace-mark"><FolderGit2 :size="16" /></div>
      <div>
        <strong>Sage 工作区</strong>
        <span :title="store.workspaceRoot">{{ store.workspaceRoot || '正在连接工作区' }}</span>
      </div>
      <button
        type="button"
        class="icon-button"
        data-testid="new-coding-session"
        title="新建会话"
        aria-label="新建会话"
        @click="startSession"
      ><Plus :size="15" /></button>
    </header>

    <label class="session-search">
      <Search :size="14" />
      <input v-model="query" type="search" placeholder="搜索会话" aria-label="搜索会话" />
    </label>
    <p v-if="sidebarError" class="sidebar-error" role="alert">{{ sidebarError }}</p>

    <div class="session-list" aria-label="会话列表">
      <p v-if="visibleSessions.length === 0" class="empty">暂无会话</p>
      <div
        v-for="session in visibleSessions"
        :key="session.session_id"
        class="session-row"
        :class="{ active: session.session_id === store.sessionId }"
      >
        <button
          v-if="renamingSessionId !== session.session_id"
          type="button"
          class="session-item"
          :aria-current="session.session_id === store.sessionId ? 'page' : undefined"
          @click="selectSession(session.session_id)"
        >
          <span class="session-status" :class="sessionState(session.session_id)"></span>
          <span class="session-copy">
            <strong>{{ session.title || '未命名会话' }}</strong>
            <small>{{ session.message_count }} 条消息 · {{ modeLabel(session.runtime_mode) }}</small>
          </span>
          <Pin v-if="session.pinned" class="pinned-mark" :size="12" aria-label="已置顶" />
        </button>
        <div v-else class="session-rename">
          <input
            v-model="renameDraft"
            class="rename-input"
            aria-label="会话名称"
            maxlength="120"
            @keydown.enter.prevent="commitRename(session.session_id)"
            @keydown.escape.prevent="renamingSessionId = null"
            @blur="commitRename(session.session_id)"
          />
        </div>
        <div class="session-actions">
          <button type="button" :title="session.pinned ? '取消置顶' : '置顶'" :aria-label="session.pinned ? '取消置顶' : '置顶'" @click="setPinned(session.session_id, !session.pinned)">
            <PinOff v-if="session.pinned" :size="13" /><Pin v-else :size="13" />
          </button>
          <button type="button" title="重命名" aria-label="重命名" @click="beginRename(session.session_id, session.title)"><Pencil :size="13" /></button>
          <button type="button" title="归档" aria-label="归档" @click="setArchived(session.session_id, true)"><Archive :size="13" /></button>
        </div>
      </div>
      <button
        v-if="!showAll && filteredSessions.length > SESSION_LIMIT"
        type="button"
        class="show-all-sessions"
        @click="showAll = true"
      >显示全部（{{ filteredSessions.length }}）</button>

      <section v-if="archivedSessions.length" class="archived-section">
        <button type="button" class="archive-toggle" @click="showArchived = !showArchived">
          <component :is="showArchived ? ChevronDown : ChevronRight" :size="13" /> 已归档（{{ archivedSessions.length }}）
        </button>
        <div v-if="showArchived">
          <div v-for="session in archivedSessions" :key="session.session_id" class="archived-row">
            <span>{{ session.title || '未命名会话' }}</span>
            <button type="button" title="恢复会话" aria-label="恢复会话" @click="setArchived(session.session_id, false)"><ArchiveRestore :size="13" /></button>
          </div>
        </div>
      </section>
    </div>

    <footer class="sidebar-footer">
      <button type="button" class="settings-link" aria-label="打开设置" @click="emit('settings')"><Settings :size="14" /> 设置</button>
      <span class="connection-state"><span class="connection-dot" :class="store.sessionId ? 'connected' : ''"></span>{{ store.sessionId ? '已连接' : '连接中' }}</span>
    </footer>
  </aside>
</template>

<style scoped>
.session-sidebar { display:flex; flex-direction:column; height:100%; min-width:0; color:#29313d; background:#f6f7f8; border-right:1px solid #dfe3e8; }
.workspace-heading { display:grid; grid-template-columns:30px minmax(0,1fr) 28px; align-items:center; gap:8px; min-height:54px; padding:8px 10px; border-bottom:1px solid #e1e5e9; }
.workspace-mark { display:grid; place-items:center; width:30px; height:30px; border:1px solid #d5dbe2; border-radius:6px; color:#1d4ed8; background:#fff; }
.workspace-heading > div:nth-child(2) { min-width:0; display:grid; gap:2px; }.workspace-heading strong { font-size:var(--sage-font-md); }.workspace-heading span { overflow:hidden; color:#7a8491; font-size:var(--sage-font-xs); text-overflow:ellipsis; white-space:nowrap; }
.icon-button { display:grid; place-items:center; width:28px; height:28px; padding:0; border:1px solid #d5dbe2; border-radius:6px; color:#374151; background:#fff; }.icon-button:hover { color:#1d4ed8; background:#f3f6fb; }
.session-search { display:flex; align-items:center; gap:7px; margin:10px; min-height:32px; padding:0 9px; border:1px solid #d8dde4; border-radius:6px; color:#8a939e; background:#fff; }
.session-search:focus-within { border-color:#93b4e8; box-shadow:0 0 0 2px #e8f0fc; }.session-search input { min-width:0; width:100%; border:0; outline:0; color:#29313d; background:transparent; font-size:var(--sage-font-sm); }
.sidebar-error { margin:0 10px 8px; color:#b42318; font-size:var(--sage-font-xs); line-height:1.5; }
.session-list { flex:1; min-height:0; overflow:auto; padding:0 6px 8px; }
.session-row { position:relative; display:flex; align-items:stretch; margin:1px 0; border-radius:6px; }.session-row:hover,.session-row:focus-within { background:#eceff3; }.session-row.active { background:#e5edf9; color:#173f7a; }
.session-item { display:grid; grid-template-columns:8px minmax(0,1fr) auto; align-items:start; gap:8px; flex:1; min-width:0; min-height:48px; padding:8px; border:0; border-radius:6px; color:inherit; background:transparent; text-align:left; }.session-rename { display:flex; align-items:center; flex:1; min-width:0; min-height:48px; padding:8px; }
.session-status { width:7px; height:7px; margin-top:5px; border-radius:50%; background:#a6afb9; }.session-status.running { background:#16a34a; box-shadow:0 0 0 3px #dcfce7; }.session-status.error { background:#dc2626; }
.session-copy { min-width:0; display:grid; gap:3px; }.session-copy strong { overflow:hidden; font-size:var(--sage-font-sm); font-weight:600; text-overflow:ellipsis; white-space:nowrap; }.session-copy small { color:#7b8490; font-size:var(--sage-font-xs); }.pinned-mark { margin-top:3px; color:#2563eb; }.rename-input { min-width:0; width:100%; height:26px; border:1px solid #8eb0e3; border-radius:4px; padding:0 5px; color:#29313d; background:#fff; font-size:var(--sage-font-sm); outline:0; }
.session-actions { display:flex; align-items:center; padding-right:5px; opacity:0; }.session-row:hover .session-actions,.session-row:focus-within .session-actions,.session-row.active .session-actions { opacity:1; }.session-actions button,.archived-row button { display:grid; place-items:center; width:25px; height:25px; padding:0; border:0; border-radius:4px; color:#657181; background:transparent; }.session-actions button:hover,.archived-row button:hover { color:#1d4ed8; background:#fff; }.session-item:focus-visible,.rename-input:focus-visible,.session-actions button:focus-visible,.archived-row button:focus-visible,.icon-button:focus-visible { outline:2px solid #2563eb; outline-offset:1px; }
.show-all-sessions { width:100%; min-height:34px; border:0; border-radius:5px; color:#2563eb; background:transparent; font-size:var(--sage-font-xs); }.show-all-sessions:hover { background:#eaf0f9; }
.archived-section { margin:8px 3px 0; border-top:1px solid #dfe3e8; padding-top:6px; }.archive-toggle { display:flex; align-items:center; gap:4px; width:100%; min-height:32px; border:0; color:#707b88; background:transparent; font-size:var(--sage-font-xs); text-align:left; }.archived-row { display:flex; align-items:center; gap:6px; min-height:34px; padding:0 4px 0 20px; color:#7b8490; font-size:var(--sage-font-xs); }.archived-row span { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.empty { margin:16px 10px; color:#8a939e; font-size:var(--sage-font-sm); }
.sidebar-footer { display:flex; align-items:center; justify-content:space-between; gap:7px; min-height:44px; padding:0 10px; border-top:1px solid #e1e5e9; color:#7b8490; font-size:var(--sage-font-xs); }.settings-link,.connection-state { display:inline-flex; align-items:center; gap:6px; }.settings-link { min-height:30px; border:0; border-radius:5px; padding:0 6px; color:#657181; background:transparent; }.settings-link:hover { color:#1d4ed8; background:#eaf0f9; }.connection-dot { width:7px; height:7px; border-radius:50%; background:#a6afb9; }.connection-dot.connected { background:#16a34a; }

.session-sidebar { color:var(--sage-text); background:var(--sage-surface-raised); border-color:var(--sage-border); }.workspace-heading { border-color:var(--sage-border); }.workspace-mark,.icon-button,.session-search { border-color:var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface); }.workspace-mark { color:var(--sage-text-secondary); }.workspace-heading span,.session-copy small { color:var(--sage-text-muted); }.icon-button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }.session-search:focus-within { border-color:var(--sage-border-strong); box-shadow:0 0 0 2px color-mix(in srgb,var(--sage-focus) 14%,transparent); }.session-search input,.rename-input { color:var(--sage-text); }.session-row:hover,.session-row:focus-within,.session-row.active { background:var(--sage-surface-muted); color:var(--sage-text); }.session-status { background:var(--sage-border-strong); }.session-status.running { background:var(--sage-success); box-shadow:0 0 0 3px var(--sage-success-bg); }.session-status.error,.sidebar-error { color:var(--sage-danger); }.pinned-mark { color:var(--sage-text-secondary); }.rename-input { border-color:var(--sage-border-strong); background:var(--sage-surface); }.session-actions button,.archived-row button,.archive-toggle,.settings-link { color:var(--sage-text-secondary); }.session-actions button:hover,.archived-row button:hover,.archive-toggle:hover,.settings-link:hover { color:var(--sage-text); background:var(--sage-surface); }.show-all-sessions { color:var(--sage-text-secondary); background:transparent; }.show-all-sessions:hover { background:var(--sage-surface-muted); }.archived-section { border-color:var(--sage-border); }.archived-row,.empty,.sidebar-footer { color:var(--sage-text-muted); }.sidebar-footer { border-color:var(--sage-border); }.connection-dot { background:var(--sage-border-strong); }.connection-dot.connected { background:var(--sage-success); }
</style>
