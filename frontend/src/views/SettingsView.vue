<script setup lang="ts">
import {
  Activity, ArrowLeft, Brain, CheckCircle2, ChevronRight, Circle, Code2, FolderGit2,
  ChartNoAxesCombined, KeyRound, Monitor, PlugZap, Settings2, Sparkles, XCircle,
} from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useCodingStore } from '../stores/coding'
import type { CodingProviderSettingsUpdate } from '../types/api'
import { useWorkbenchPreferences } from '../composables/useWorkbenchPreferences'
import { wireNaiveUI } from '../composables/useNaiveUI'

wireNaiveUI()

export type SettingsSection =
  | 'appearance' | 'providers' | 'models' | 'usage' | 'skills' | 'mcp' | 'memory'
  | 'context' | 'sessions' | 'workspace' | 'runs'

const sections: Array<{ id: SettingsSection; label: string; icon: typeof Monitor }> = [
  { id: 'appearance', label: '外观', icon: Monitor },
  { id: 'providers', label: 'Provider', icon: KeyRound },
  { id: 'models', label: '模型', icon: Sparkles },
  { id: 'usage', label: '用量', icon: ChartNoAxesCombined },
  { id: 'skills', label: 'Skills', icon: Code2 },
  { id: 'mcp', label: 'MCP', icon: PlugZap },
  { id: 'memory', label: '记忆', icon: Brain },
  { id: 'context', label: '上下文', icon: Settings2 },
  { id: 'sessions', label: '会话', icon: Activity },
  { id: 'workspace', label: '工作区与 Git', icon: FolderGit2 },
  { id: 'runs', label: '运行与任务', icon: Activity },
]

const route = useRoute()
const router = useRouter()
const store = useCodingStore()
const { showToolProcess, themeMode } = useWorkbenchPreferences()
const sessionError = ref('')
const mobileSectionOpen = ref(false)
const providerDraft = ref('')
const providerMessage = ref('')
const usageRanges = ['7d', '30d', '90d', '365d'] as const

const activeSection = computed(() => route.params.section as SettingsSection)
const activeMeta = computed(() => sections.find((section) => section.id === activeSection.value) ?? sections[0])
const currentModel = computed(() => store.models.find((model) => model.id === store.currentModelId))
const latestActiveSessionId = computed(() => store.codingSessions
  .filter((session) => !session.archived)
  .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))[0]?.session_id || '')

function navigate(section: SettingsSection) {
  mobileSectionOpen.value = false
  void router.push(`/settings/${section}`)
}

function goBackToChat() {
  const current = store.codingSessions.find((session) => session.session_id === store.sessionId)
  const sessionId = current && !current.archived ? current.session_id : latestActiveSessionId.value
  void router.push(sessionId ? `/coding/session/${encodeURIComponent(sessionId)}` : '/coding')
}

async function toggleSessionPinned(sessionId: string, pinned: boolean) {
  sessionError.value = ''
  try {
    await store.setSessionPinned(sessionId, pinned)
  } catch (error) {
    sessionError.value = `无法${pinned ? '置顶' : '取消置顶'}会话：${error instanceof Error ? error.message : String(error)}`
  }
}

async function toggleSessionArchived(sessionId: string, archived: boolean) {
  sessionError.value = ''
  if (sessionId === store.sessionId && !archived) return
  try {
    await store.setSessionArchived(sessionId, archived)
  } catch (error) {
    sessionError.value = `无法${archived ? '归档' : '恢复'}会话：${error instanceof Error ? error.message : String(error)}`
  }
}

function statusLabel(status: string) {
  return ({ completed: '已完成', running: '运行中', error: '失败', cancelled: '已取消', interrupted: '已中断' } as Record<string, string>)[status] ?? status
}

function readableTokens(value: number | null | undefined) {
  if (!value) return '未配置'
  return value.toLocaleString()
}

function readableUsage(value: number | null | undefined) {
  return value === null || value === undefined ? '--' : value.toLocaleString()
}

function syncProviderDraft() {
  const settings = store.providerSettings
  if (!settings) return
  providerDraft.value = JSON.stringify({
    version: settings.version,
    default_model: settings.default_model,
    providers: settings.providers.map((provider) => ({
      id: provider.id,
      label: provider.label,
      api_mode: provider.api_mode,
      base_url: provider.base_url,
      api_key_env: provider.api_key_env,
      models: provider.models.map((model) => ({
        id: model.id,
        label: model.label,
        ...(model.context_window_tokens !== null && model.output_reserve_tokens !== null
          ? {
              context_window_tokens: model.context_window_tokens,
              output_reserve_tokens: model.output_reserve_tokens,
            }
          : {}),
        reasoning: model.reasoning,
      })),
    })),
  }, null, 2)
}

async function saveProviderDraft() {
  providerMessage.value = ''
  try {
    const parsed: unknown = JSON.parse(providerDraft.value)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('配置根节点必须是 JSON 对象')
    }
    const saved = await store.saveProviderSettings(parsed as CodingProviderSettingsUpdate)
    providerMessage.value = saved ? 'Provider 配置已保存' : store.providerSettingsError
    if (saved) syncProviderDraft()
  } catch (error) {
    providerMessage.value = error instanceof Error ? error.message : String(error)
  }
}

onMounted(() => {
  void Promise.all([
    store.loadSkills(), store.loadMcpServers(), store.loadModels(), store.loadSessions().catch(() => {}),
    store.loadProviderSettings().then(syncProviderDraft), store.loadUsage(),
  ])
  if (store.sessionId) {
    void Promise.all([store.loadRuns(), store.loadMemoryProposals(), store.loadContext(), store.loadGitStatus()])
  }
})
</script>

<template>
  <main class="settings-view">
    <header class="settings-header">
      <button class="back-button" type="button" @click="goBackToChat"><ArrowLeft :size="16" /> 返回聊天</button>
      <div><strong>Sage</strong><span>设置中心</span></div>
    </header>

    <div class="settings-shell">
      <nav class="settings-nav" aria-label="设置分类">
        <button v-for="section in sections" :key="section.id" type="button" :class="{ active: activeSection === section.id }" :aria-current="activeSection === section.id ? 'page' : undefined" @click="navigate(section.id)">
          <component :is="section.icon" :size="16" /><span>{{ section.label }}</span><ChevronRight :size="14" />
        </button>
      </nav>

      <section class="settings-content" :aria-label="`${activeMeta.label}设置`">
        <header class="content-heading"><component :is="activeMeta.icon" :size="18" /><div><h1>{{ activeMeta.label }}</h1><p>当前单工作区私测配置</p></div><button class="mobile-section-trigger" type="button" @click="mobileSectionOpen = true">全部设置</button></header>

        <div v-if="mobileSectionOpen" class="mobile-section-sheet" role="dialog" aria-modal="true" aria-label="设置分类">
          <header><strong>设置</strong><button type="button" aria-label="关闭设置分类" @click="mobileSectionOpen = false">关闭</button></header>
          <button v-for="section in sections" :key="section.id" type="button" :class="{ active: activeSection === section.id }" @click="navigate(section.id)"><component :is="section.icon" :size="16" />{{ section.label }}<ChevronRight :size="15" /></button>
        </div>

        <section v-if="activeSection === 'appearance'" class="settings-section">
          <h2>显示</h2>
          <label class="setting-row"><span><strong>主题</strong><small>主题偏好保存在此浏览器</small></span><select v-model="themeMode" aria-label="主题"><option value="light">浅色</option><option value="dark">深色</option><option value="system">跟随系统</option></select></label>
          <label class="setting-row"><span><strong>显示工具过程</strong><small>在聊天时间线中显示思考、工具和审批细节</small></span><input v-model="showToolProcess" type="checkbox" aria-label="显示工具过程" /></label>
        </section>

        <section v-else-if="activeSection === 'models'" class="settings-section">
          <h2>模型</h2><p class="section-note">模型选择会作用于当前会话；没有活动会话时仅展示可用模型。</p>
          <p v-if="store.models.length === 0" class="empty">暂无可用模型</p>
          <label v-for="model in store.models" :key="model.id" class="choice-row" :class="{ selected: model.id === store.currentModelId }"><input type="radio" name="model" :value="model.id" :checked="model.id === store.currentModelId" :disabled="!store.sessionId || store.isThinking" @change="store.changeModel(model.id)" /><span><strong>{{ model.label }}</strong><small>{{ model.provider }} · 上下文 {{ readableTokens(model.context_window_tokens) }} · 推理 {{ model.reasoning_modes.length ? model.reasoning_modes.join(' / ') : '不支持' }}</small></span><CheckCircle2 v-if="model.id === store.currentModelId" :size="17" /></label>
        </section>

        <section v-else-if="activeSection === 'providers'" class="settings-section">
          <h2>Provider</h2><p class="section-note">只管理非敏感目录。API Key 必须通过服务端环境变量配置，网页不会接收或回显密钥。</p>
          <p v-if="!store.providerSettings" class="empty">Provider 配置暂不可用</p>
          <article v-for="provider in store.providerSettings?.providers || []" :key="provider.id" class="resource-row provider-row"><span class="status-dot" :class="{ ready: provider.api_key_configured }"></span><div><strong>{{ provider.label }}</strong><p>{{ provider.api_mode }} · {{ provider.base_url }}</p><small>{{ provider.api_key_env }} · {{ provider.api_key_configured ? '环境变量已配置' : '环境变量未配置' }} · {{ provider.models.length }} 个模型</small></div><span class="read-only">{{ store.providerSettings?.source === 'deployment_json' ? '部署托管' : '项目配置' }}</span></article>
          <details v-if="store.providerSettings?.editable" class="provider-editor"><summary>编辑 .sage/settings.json</summary><p>仅允许 Provider、模型、context 和 reasoning 描述；请勿粘贴 API Key。</p><textarea v-model="providerDraft" aria-label="Provider JSON 配置" spellcheck="false" autocomplete="off"></textarea><div><button class="command" type="button" @click="saveProviderDraft">保存配置</button><span v-if="providerMessage" :class="{ error: !providerMessage.includes('已保存') }" role="status">{{ providerMessage }}</span></div></details>
          <p v-if="store.providerSettingsError" class="error" role="alert">{{ store.providerSettingsError }}</p>
        </section>

        <section v-else-if="activeSection === 'usage'" class="settings-section">
          <h2>用量</h2><p class="section-note">仅统计 Provider 实际返回的 token 元数据；未提供的字段保持为空，费用不会估算。</p>
          <div class="range-control" aria-label="用量范围"><button v-for="range in usageRanges" :key="range" type="button" :class="{ active: store.usageRange === range }" @click="store.loadUsage(range)">{{ range }}</button></div>
          <dl v-if="store.usageSummary" class="fact-grid usage-facts"><div><dt>输入 tokens</dt><dd>{{ readableUsage(store.usageSummary.input_tokens) }}</dd></div><div><dt>输出 tokens</dt><dd>{{ readableUsage(store.usageSummary.output_tokens) }}</dd></div><div><dt>缓存读取</dt><dd>{{ readableUsage(store.usageSummary.cache_read_tokens) }}</dd></div><div><dt>会话 / 请求</dt><dd>{{ store.usageSummary.session_count }} / {{ store.usageSummary.request_count }}</dd></div><div><dt>缓存命中率</dt><dd>{{ store.usageSummary.cache_hit_ratio === null ? '--' : `${(store.usageSummary.cache_hit_ratio * 100).toFixed(1)}%` }}</dd></div><div><dt>费用</dt><dd>{{ store.usageSummary.cost === null ? '--' : store.usageSummary.cost }}</dd></div></dl>
          <p v-if="store.usageSummary?.request_count === 0" class="empty">当前范围内暂无 Provider 用量数据</p>
          <div v-else class="usage-list"><div v-for="model in store.usageSummary?.models || []" :key="model.model"><strong>{{ model.model }}</strong><span>输入 {{ readableUsage(model.input_tokens) }} · 输出 {{ readableUsage(model.output_tokens) }} · 缓存 {{ readableUsage(model.cache_read_tokens) }}</span></div></div>
          <p v-if="store.usageError" class="error" role="alert">{{ store.usageError }}</p>
        </section>

        <section v-else-if="activeSection === 'skills'" class="settings-section">
          <h2>Skills</h2><p class="section-note">通过聊天输入框的 <code>/skill</code> 显式调用。网页端不提供外部安装或工具授权修改。</p>
          <p v-if="store.skills.length === 0" class="empty">暂无可发现的 Skill</p>
          <article v-for="skill in store.skills" :key="skill.name" class="resource-row"><Code2 :size="16" /><div><strong>/{{ skill.name }}</strong><p>{{ skill.description }}</p><small>来源：{{ skill.source }} · 参数：{{ skill.argument_hint || '无' }}</small></div><span class="read-only">只读</span></article>
        </section>

        <section v-else-if="activeSection === 'mcp'" class="settings-section">
          <h2>MCP</h2><p class="section-note">V6.9 仅展示已发现的 MCP 配置清单；当前 API 不提供工具数量或运行错误详情。密钥与网页安装将在云端权限模型完成后开放。</p>
          <p v-if="store.mcpServers.length === 0" class="empty">暂无 MCP 配置</p>
          <article v-for="server in store.mcpServers" :key="server.name" class="resource-row"><span class="status-dot" :class="server.status"></span><div><strong>{{ server.name }}</strong><p>{{ server.transport }}</p></div><span class="read-only">{{ server.status }} · 只读</span></article>
        </section>

        <section v-else-if="activeSection === 'memory'" class="settings-section">
          <h2>记忆提案</h2><p class="section-note">推断记忆必须人工批准。<code>/remember</code> 用于记录明确约定。</p>
          <p v-if="store.memoryProposals.length === 0" class="empty">暂无待审核记忆提案</p>
          <article v-for="proposal in store.memoryProposals" :key="proposal.proposal_id" class="memory-row"><div><p v-for="candidate in proposal.candidates" :key="candidate.content">{{ candidate.content }}</p><small>来源：{{ proposal.candidates[0]?.source || 'dream' }}</small></div><div class="memory-actions"><button type="button" :disabled="!!store.memoryProposalBusy[proposal.proposal_id]" @click="store.approveMemoryProposal(proposal.proposal_id, proposal.revision)">批准</button><button type="button" :disabled="!!store.memoryProposalBusy[proposal.proposal_id]" @click="store.rejectMemoryProposal(proposal.proposal_id, proposal.revision)">拒绝</button></div></article>
          <p v-if="store.memoryProposalError" class="error" role="alert">{{ store.memoryProposalError }}</p>
        </section>

        <section v-else-if="activeSection === 'context'" class="settings-section">
          <h2>上下文</h2><p class="section-note">当前会话的上下文快照和压缩状态。</p>
          <dl class="fact-grid"><div><dt>当前模型</dt><dd>{{ currentModel?.label || store.contextSnapshot?.model_id || '未配置' }}</dd></div><div><dt>已使用 tokens</dt><dd>{{ readableTokens(store.contextSnapshot?.used_tokens) }}</dd></div><div><dt>有效窗口</dt><dd>{{ readableTokens(store.contextSnapshot?.effective_limit_tokens) }}</dd></div><div><dt>恢复状态</dt><dd>{{ store.contextSnapshot?.resume_status || '未知' }}</dd></div></dl>
          <button class="command" type="button" :disabled="!store.contextCompactable || store.contextBusy || store.isThinking" @click="store.compactContext()">{{ store.contextBusy ? '正在压缩...' : '压缩当前上下文' }}</button><p v-if="store.compactionError" class="error" role="alert">{{ store.compactionError }}</p>
        </section>

        <section v-else-if="activeSection === 'sessions'" class="settings-section">
          <h2>会话</h2><p class="section-note">管理会话名称、置顶和归档状态。</p>
          <p v-if="store.codingSessions.length === 0" class="empty">暂无会话</p>
          <article v-for="session in store.codingSessions" :key="session.session_id" class="session-row"><div><strong>{{ session.title || '未命名会话' }}</strong><small>{{ session.message_count }} 条消息 · {{ session.updated_at || '时间未知' }}</small></div><span v-if="session.archived" class="read-only">已归档</span><button type="button" @click="toggleSessionPinned(session.session_id, !session.pinned)">{{ session.pinned ? '取消置顶' : '置顶' }}</button><button type="button" :disabled="session.session_id === store.sessionId && !session.archived" :title="session.session_id === store.sessionId && !session.archived ? '请在聊天页归档当前会话' : undefined" @click="toggleSessionArchived(session.session_id, !session.archived)">{{ session.archived ? '恢复' : '归档' }}</button></article>
          <p v-if="sessionError" class="error" role="alert">{{ sessionError }}</p>
        </section>

        <section v-else-if="activeSection === 'workspace'" class="settings-section">
          <h2>工作区与 Git</h2><p class="section-note">当前工作区只读状态概览。文件和 Diff 从聊天界面的临时面板打开。</p>
          <dl class="fact-grid"><div><dt>工作区</dt><dd class="path">{{ store.workspaceRoot || '未连接' }}</dd></div><div><dt>Git 分支</dt><dd>{{ store.gitStatus.is_git ? store.gitStatus.branch : '非 Git 工作区' }}</dd></div><div><dt>待处理变更</dt><dd>{{ store.gitStatus.dirty_count }}</dd></div></dl>
          <ul v-if="store.gitStatus.changed_files.length" class="path-list"><li v-for="path in store.gitStatus.changed_files" :key="path">{{ path }}</li></ul>
        </section>

        <section v-else class="settings-section">
          <h2>运行与任务</h2><p class="section-note">查看持久化运行记录。终端将在 V7 的沙箱、配额和审计机制完成后开放。</p>
          <p v-if="store.runs.length === 0" class="empty">暂无运行记录</p>
          <button v-for="run in store.runs" :key="run.run_id" type="button" class="run-row" @click="store.loadRunDetail(run.run_id)"><component :is="run.status === 'completed' ? CheckCircle2 : run.status === 'error' ? XCircle : Circle" :size="16" /><span><strong>{{ run.run_id }}</strong><small>{{ run.tool_count }} 次工具调用 · {{ run.event_count }} 个事件</small></span><span :class="['run-state', run.status]">{{ statusLabel(run.status) }}</span></button>
          <div v-if="store.selectedRun" class="run-detail"><p v-for="(item, index) in store.selectedRun.timeline.slice(-8)" :key="index"><strong>{{ item.title }}</strong><span>{{ item.detail }}</span></p></div>
          <div class="terminal-note"><span>终端</span><small>V7 沙箱完成后开放</small></div>
        </section>
      </section>
    </div>
  </main>
</template>

<style scoped>
.settings-view { min-height:100dvh; color:#26313d; background:#f8f9fa; }.settings-header { display:flex; align-items:center; gap:18px; min-height:54px; padding:0 20px; border-bottom:1px solid #dfe3e8; background:#fff; }.settings-header > div { display:flex; gap:8px; color:#7c8794; font-size:12px; }.settings-header strong { color:#202936; font-size:15px; }.back-button { display:inline-flex; align-items:center; gap:6px; min-height:30px; padding:0 8px; border:0; border-radius:6px; color:#596675; background:transparent; font-size:12px; }.back-button:hover { color:#1f2937; background:#f1f3f5; }.settings-shell { display:grid; grid-template-columns:220px minmax(0,960px); justify-content:center; min-height:calc(100dvh - 54px); }.settings-nav { padding:16px 10px; border-right:1px solid #e1e5e9; background:#f5f6f7; }.settings-nav button { display:grid; grid-template-columns:18px minmax(0,1fr) 14px; align-items:center; gap:8px; width:100%; min-height:36px; padding:0 9px; border:0; border-radius:6px; color:#5c6875; background:transparent; font-size:12px; text-align:left; }.settings-nav button:hover { background:#eceff2; color:#29313d; }.settings-nav button.active { color:#1f2937; background:#fff; box-shadow:0 1px 2px rgba(15,23,42,.05); }.settings-nav button svg:last-child { opacity:0; }.settings-nav button.active svg:last-child { opacity:1; }.settings-content { position:relative; min-width:0; padding:42px clamp(20px,5vw,56px); background:#fff; }.content-heading { display:flex; align-items:flex-start; gap:10px; padding-bottom:22px; border-bottom:1px solid #edf0f2; }.content-heading h1 { margin:0; font-size:20px; }.content-heading p { margin:4px 0 0; color:#7b8794; font-size:12px; }.mobile-section-trigger { display:none; margin-left:auto; min-height:30px; border:1px solid #d5dbe2; border-radius:6px; padding:0 8px; color:#536171; background:#fff; font-size:11px; }.mobile-section-sheet { display:none; }.settings-section { padding:26px 0; border-bottom:1px solid #edf0f2; }.settings-section h2 { margin:0 0 7px; color:#29313d; font-size:15px; }.section-note,.empty { margin:0 0 18px; color:#718093; font-size:12px; line-height:1.65; }.setting-row,.choice-row,.resource-row,.session-row,.run-row { display:flex; align-items:center; gap:12px; min-height:54px; border-bottom:1px solid #edf0f2; }.setting-row > span,.choice-row > span,.resource-row > div,.session-row > div,.run-row > span { display:grid; gap:3px; flex:1; min-width:0; }.setting-row strong,.choice-row strong,.resource-row strong,.session-row strong,.run-row strong { font-size:13px; }.setting-row small,.choice-row small,.resource-row small,.session-row small,.run-row small { overflow:hidden; color:#7b8794; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }.setting-row select { min-width:110px; height:30px; border:1px solid #d5dbe2; border-radius:6px; padding:0 7px; color:#34404e; background:#fff; font-size:12px; }.setting-row input[type="checkbox"] { width:16px; height:16px; accent-color:#29313d; }.choice-row { padding:8px 10px; border:1px solid transparent; border-radius:6px; }.choice-row:hover { background:#f8f9fa; }.choice-row.selected { border-color:#dfe3e8; background:#f8f9fa; }.choice-row input { accent-color:#29313d; }.resource-row { padding:10px 0; }.resource-row > svg { color:#637181; }.resource-row p { margin:0; color:#667585; font-size:12px; }.read-only { flex:none; padding:2px 6px; border:1px solid #dfe3e8; border-radius:4px; color:#718093; font-size:10px; }.status-dot { width:8px; height:8px; border-radius:50%; background:#a7afb9; }.status-dot.connected,.status-dot.ready { background:#16a34a; }.memory-row { display:flex; justify-content:space-between; gap:16px; padding:12px 0; border-bottom:1px solid #edf0f2; }.memory-row p { margin:0 0 6px; font-size:12px; line-height:1.55; }.memory-row small { color:#7b8794; font-size:11px; }.memory-actions { display:flex; align-items:flex-start; gap:6px; }.memory-actions button,.session-row button,.command { min-height:28px; border:1px solid #d5dbe2; border-radius:6px; padding:0 9px; color:#455363; background:#fff; font-size:11px; }.memory-actions button:hover,.session-row button:hover,.command:hover { background:#f5f7f9; }.fact-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin:0 0 18px; }.fact-grid > div { min-width:0; padding:10px; border:1px solid #e4e8ec; border-radius:6px; }.fact-grid dt { color:#7b8794; font-size:10px; }.fact-grid dd { overflow:hidden; margin:4px 0 0; color:#334155; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }.fact-grid dd.path { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }.path-list { margin:0; padding:0; list-style:none; }.path-list li { overflow:hidden; padding:7px 0; border-bottom:1px solid #edf0f2; color:#556474; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }.run-row { width:100%; padding:9px 0; border-left:0; border-right:0; border-top:0; background:transparent; color:inherit; text-align:left; }.run-row:hover { background:#f8f9fa; }.run-state { margin-left:auto; color:#7b8794; font-size:11px; }.run-state.running { color:#16803d; }.run-state.error { color:#b42318; }.run-detail { margin:10px 0; border-left:2px solid #e3e7eb; padding-left:10px; }.run-detail p { display:grid; gap:2px; margin:0 0 9px; color:#657384; font-size:11px; }.run-detail strong { color:#34404e; }.terminal-note { display:flex; align-items:center; justify-content:space-between; margin-top:18px; border:1px dashed #cfd6dd; border-radius:6px; padding:10px; color:#5f6d7a; font-size:12px; }.terminal-note small { color:#7b8794; font-size:11px; }.error { color:#b42318; font-size:12px; } code { padding:1px 4px; border-radius:3px; background:#f2f4f6; color:#44505e; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.92em; }

.provider-editor { margin-top:18px; border-top:1px solid var(--sage-border); padding-top:14px; }
.provider-editor summary { cursor:pointer; color:var(--sage-text-secondary); font-size:12px; font-weight:700; }
.provider-editor p { color:var(--sage-text-muted); font-size:11px; }
.provider-editor textarea { width:100%; min-height:320px; resize:vertical; border:1px solid var(--sage-border); border-radius:var(--sage-radius); padding:12px; color:var(--sage-code-text); background:var(--sage-code-bg); font-family:var(--sage-font-mono); font-size:11px; line-height:1.55; }
.provider-editor > div { display:flex; align-items:center; gap:10px; margin-top:8px; }
.range-control { display:flex; width:fit-content; margin:0 0 16px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); padding:2px; }
.range-control button { min-width:42px; height:26px; border:0; border-radius:var(--sage-radius-sm); color:var(--sage-text-muted); background:transparent; font-size:10px; }
.range-control button.active { color:var(--sage-text); background:var(--sage-surface-muted); font-weight:700; }
.usage-facts { grid-template-columns:repeat(3,minmax(0,1fr)); }
.usage-list { display:grid; gap:0; border-top:1px solid var(--sage-border); }
.usage-list > div { display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:46px; border-bottom:1px solid var(--sage-border); }
.usage-list strong { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-family:var(--sage-font-mono); font-size:11px; }
.usage-list span { color:var(--sage-text-muted); font-size:10px; white-space:nowrap; }
@media (max-width:767px) { .settings-header { padding:0 12px; }.settings-shell { display:block; }.settings-nav { display:none; }.settings-content { padding:24px 16px; }.content-heading { align-items:center; }.mobile-section-trigger { display:block; }.mobile-section-sheet { position:fixed; z-index:20; inset:0; display:grid; align-content:start; gap:2px; padding:16px; background:#fff; overflow:auto; }.mobile-section-sheet header { display:flex; align-items:center; justify-content:space-between; min-height:42px; margin-bottom:8px; border-bottom:1px solid #e4e8ec; }.mobile-section-sheet header button { min-height:30px; border:0; border-radius:6px; color:#566575; background:#f3f5f7; font-size:12px; }.mobile-section-sheet > button { display:grid; grid-template-columns:18px minmax(0,1fr) 16px; align-items:center; gap:9px; min-height:46px; border:0; border-bottom:1px solid #edf0f2; color:#3c4958; background:#fff; text-align:left; }.mobile-section-sheet > button.active { font-weight:700; }.memory-row { display:grid; }.memory-actions { justify-content:flex-start; }.fact-grid { grid-template-columns:1fr; } }

/* Settings retains its own information density while consuming the shared Sage palette. */
.settings-view { color:var(--sage-text); background:var(--sage-bg); }.settings-header { border-color:var(--sage-border); background:var(--sage-surface); }.settings-header > div,.back-button { color:var(--sage-text-muted); }.settings-header strong { color:var(--sage-text); }.back-button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }.settings-nav { border-color:var(--sage-border); background:var(--sage-surface-raised); }.settings-nav button { color:var(--sage-text-secondary); }.settings-nav button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }.settings-nav button.active { color:var(--sage-text); background:var(--sage-surface); box-shadow:none; }.settings-content { background:var(--sage-surface); }.content-heading,.settings-section,.setting-row,.choice-row,.resource-row,.session-row,.run-row { border-color:var(--sage-border); }.content-heading h1,.settings-section h2 { color:var(--sage-text); }.content-heading p,.section-note,.empty,.setting-row small,.choice-row small,.resource-row small,.session-row small,.run-row small { color:var(--sage-text-muted); }.mobile-section-trigger,.setting-row select,.memory-actions button,.session-row button,.command { border-color:var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface); }.choice-row:hover,.run-row:hover { background:var(--sage-surface-muted); }.choice-row.selected { border-color:var(--sage-border-strong); background:var(--sage-surface-muted); }.resource-row > svg,.resource-row p { color:var(--sage-text-secondary); }.read-only { border-color:var(--sage-border); color:var(--sage-text-muted); }.status-dot { background:var(--sage-border-strong); }.status-dot.connected,.status-dot.ready { background:var(--sage-success); }.fact-grid > div,.terminal-note { border-color:var(--sage-border); }.fact-grid dt,.path-list li,.run-detail p,.terminal-note small { color:var(--sage-text-muted); }.fact-grid dd,.run-detail strong { color:var(--sage-text-secondary); }.run-state.running { color:var(--sage-success); }.run-state.error,.error { color:var(--sage-danger); } code { background:var(--sage-surface-muted); color:var(--sage-text-secondary); }
@media (max-width:767px) { .mobile-section-sheet { background:var(--sage-surface); }.mobile-section-sheet header { border-color:var(--sage-border); }.mobile-section-sheet header button { color:var(--sage-text-secondary); background:var(--sage-surface-muted); }.mobile-section-sheet > button { border-color:var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface); } }
</style>
