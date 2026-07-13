<script setup lang="ts">
import {
  Activity,
  Brain,
  CheckCircle2,
  FileCode2,
  Files,
  GitCompareArrows,
  PlugZap,
  TerminalSquare,
  XCircle,
  Zap,
} from 'lucide-vue-next'
import { computed, nextTick, ref } from 'vue'
import { useCodingStore } from '../../../stores/coding'
import type { CodingSkillSummary } from '../../../types/api'
import CodingFileTree from '../files/CodingFileTree.vue'

type InspectorTab = 'files' | 'changes' | 'runs' | 'memory'

const store = useCodingStore()
const emit = defineEmits<{ useSkill: [command: string] }>()
const activeTab = ref<InspectorTab>('files')

const tabs = [
  { id: 'files' as const, label: '文件', icon: Files },
  { id: 'changes' as const, label: '变更', icon: GitCompareArrows },
  { id: 'runs' as const, label: '运行', icon: Activity },
  { id: 'memory' as const, label: '记忆', icon: Brain },
]

const changedFiles = computed(() => store.gitStatus.changed_files ?? [])

function selectTab(tab: InspectorTab) {
  activeTab.value = tab
}

function handleTabKeydown(event: KeyboardEvent, currentIndex: number) {
  let nextIndex = currentIndex
  if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length
  else if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length
  else if (event.key === 'Home') nextIndex = 0
  else if (event.key === 'End') nextIndex = tabs.length - 1
  else return

  event.preventDefault()
  const tablist = (event.currentTarget as HTMLElement).parentElement
  activeTab.value = tabs[nextIndex].id
  void nextTick(() => {
    tablist?.querySelector<HTMLButtonElement>(`#inspector-tab-${tabs[nextIndex].id}`)?.focus()
  })
}

function selectSkill(skill: CodingSkillSummary) {
  emit('useSkill', `/${skill.name} `)
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    completed: '已完成', running: '运行中', error: '失败', cancelled: '已取消', interrupted: '已中断',
  }
  return labels[status] ?? status
}
</script>

<template>
  <aside class="inspector" aria-label="工作区检查器">
    <div class="inspector-tabs" role="tablist" aria-label="检查器视图">
      <button
        v-for="(tab, index) in tabs"
        :key="tab.id"
        type="button"
        role="tab"
        :id="`inspector-tab-${tab.id}`"
        :data-tab="tab.id"
        :aria-selected="activeTab === tab.id"
        :aria-controls="`inspector-panel-${tab.id}`"
        :tabindex="activeTab === tab.id ? 0 : -1"
        :class="{ active: activeTab === tab.id }"
        @click="selectTab(tab.id)"
        @keydown="handleTabKeydown($event, index)"
      >
        <component :is="tab.icon" :size="14" />
        <span>{{ tab.label }}</span>
      </button>
    </div>

    <div
      class="inspector-content"
      role="tabpanel"
      tabindex="0"
      :id="`inspector-panel-${activeTab}`"
      :aria-labelledby="`inspector-tab-${activeTab}`"
    >
      <CodingFileTree v-if="activeTab === 'files'" />

      <section v-else-if="activeTab === 'changes'" class="inspector-section">
        <header class="section-title">
          <GitCompareArrows :size="15" />
          <h3>工作区变更</h3>
          <span>{{ store.gitStatus.dirty_count }}</span>
        </header>
        <p v-if="changedFiles.length === 0" class="muted">当前工作区没有待处理变更</p>
        <button
          v-for="path in changedFiles"
          :key="path"
          type="button"
          class="change-row"
          :title="path"
          @click="store.loadFilePreview(path); activeTab = 'files'"
        >
          <FileCode2 :size="14" />
          <span>{{ path }}</span>
        </button>
        <button
          v-if="store.lastDiffInfo"
          type="button"
          class="secondary-command"
          @click="store.openDiffDrawer"
        >
          <GitCompareArrows :size="14" /> 查看本次运行 Diff
        </button>
      </section>

      <section v-else-if="activeTab === 'runs'" class="inspector-section">
        <header class="section-title"><Activity :size="15" /><h3>运行记录</h3></header>
        <p v-if="store.runs.length === 0" class="muted">暂无运行记录</p>
        <button
          v-for="run in store.runs"
          :key="run.run_id"
          type="button"
          class="run-row"
          @click="store.loadRunDetail(run.run_id)"
        >
          <component :is="run.status === 'completed' ? CheckCircle2 : run.status === 'error' ? XCircle : Activity" :size="14" />
          <span class="run-name">{{ run.run_id }}</span>
          <span class="run-state" :class="run.status">{{ statusLabel(run.status) }}</span>
        </button>
        <div v-if="store.selectedRun" class="run-detail">
          <p v-for="(item, index) in store.selectedRun.timeline?.slice(-6)" :key="index">
            <strong>{{ item.title }}</strong><span>{{ item.detail }}</span>
          </p>
        </div>

        <header class="section-title subsection"><Zap :size="15" /><h3>Skills</h3></header>
        <p v-if="store.skills.length === 0" class="muted">暂无可用 Skill</p>
        <button v-for="skill in store.skills" :key="skill.name" class="skill-row" type="button" @click="selectSkill(skill)">
          <Zap :size="13" /><span>/{{ skill.name }}</span><small>{{ skill.description }}</small>
        </button>

        <header class="section-title subsection"><PlugZap :size="15" /><h3>MCP 连接</h3></header>
        <p v-if="store.mcpServers.length === 0" class="muted">暂无 MCP 连接</p>
        <div v-for="server in store.mcpServers" :key="server.name" class="mcp-row">
          <span class="status-dot" :class="server.status"></span><span>{{ server.name }}</span><small>{{ server.transport }}</small>
        </div>

        <div class="terminal-disabled" aria-disabled="true">
          <TerminalSquare :size="15" />
          <div><strong>终端</strong><span>终端将在 V7 沙箱就绪后开放</span></div>
        </div>
      </section>

      <section v-else class="inspector-section">
        <header class="section-title"><Brain :size="15" /><h3>记忆提案</h3><span>{{ store.memoryProposals.length }}</span></header>
        <p v-if="store.memoryProposals.length === 0" class="muted">暂无待审核记忆，可使用 /remember 记录明确约定</p>
        <article v-for="proposal in store.memoryProposals" :key="proposal.proposal_id" class="memory-row">
          <p v-for="candidate in proposal.candidates" :key="candidate.content">{{ candidate.content }}</p>
          <small>来源：{{ proposal.candidates[0]?.source || 'dream' }}</small>
          <div>
            <button type="button" :disabled="!!store.memoryProposalBusy[proposal.proposal_id]" @click="store.approveMemoryProposal(proposal.proposal_id, proposal.revision)">批准</button>
            <button type="button" :disabled="!!store.memoryProposalBusy[proposal.proposal_id]" @click="store.rejectMemoryProposal(proposal.proposal_id, proposal.revision)">拒绝</button>
          </div>
        </article>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.inspector { display:flex; flex-direction:column; height:100%; min-width:0; background:#fff; }
.inspector-tabs { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); min-height:42px; border-bottom:1px solid #e5e7eb; }
.inspector-tabs button { display:flex; align-items:center; justify-content:center; gap:5px; min-width:0; padding:0 5px; border:0; border-bottom:2px solid transparent; color:#6b7280; background:#fff; font-size:12px; }
.inspector-tabs button.active { color:#1d4ed8; border-bottom-color:#2563eb; }
.inspector-tabs button:focus-visible { outline:2px solid #2563eb; outline-offset:-2px; }
.inspector-content { min-height:0; flex:1; overflow:auto; }
.inspector-content :deep(.file-tree) { border-left:0; }
.inspector-section { padding:12px; }
.section-title { display:flex; align-items:center; gap:7px; margin:0 0 10px; color:#374151; }
.section-title h3 { margin:0; flex:1; font-size:12px; font-weight:700; }
.section-title > span { color:#6b7280; font-size:11px; }
.subsection { margin-top:20px; padding-top:14px; border-top:1px solid #eef0f3; }
.muted { margin:14px 0; color:#8a939f; font-size:12px; line-height:1.5; }
.change-row,.run-row,.skill-row { display:flex; align-items:center; gap:7px; width:100%; min-height:32px; padding:5px 7px; border:0; border-radius:5px; color:#374151; background:transparent; text-align:left; font-size:12px; }
.change-row:hover,.run-row:hover,.skill-row:hover { background:#f3f5f7; }
.change-row span,.run-name,.skill-row small { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.run-name { flex:1; font-family:'SF Mono',ui-monospace,monospace; }
.run-state { color:#6b7280; font-size:10px; }.run-state.running { color:#15803d; }.run-state.error { color:#b91c1c; }
.run-detail { margin:6px 0 0 21px; border-left:1px solid #dbe1e7; padding-left:9px; }
.run-detail p { display:grid; gap:2px; margin:0 0 8px; color:#374151; font-size:11px; }.run-detail span { color:#7b8490; overflow-wrap:anywhere; }
.skill-row span { flex:none; color:#1d4ed8; font-family:'SF Mono',ui-monospace,monospace; }.skill-row small { flex:1; color:#7b8490; }
.mcp-row { display:flex; align-items:center; gap:7px; min-height:28px; font-size:12px; }.mcp-row small { margin-left:auto; color:#8a939f; }
.status-dot { width:7px; height:7px; border-radius:50%; background:#9ca3af; }.status-dot.connected,.status-dot.ready { background:#16a34a; }
.terminal-disabled { display:flex; gap:9px; margin-top:18px; padding:10px; border:1px dashed #cfd5dc; border-radius:6px; color:#7b8490; background:#f8f9fa; }
.terminal-disabled div { display:grid; gap:2px; }.terminal-disabled strong { color:#4b5563; font-size:12px; }.terminal-disabled span { font-size:11px; }
.secondary-command { display:flex; align-items:center; justify-content:center; gap:6px; width:100%; margin-top:12px; min-height:32px; border:1px solid #cfd6df; border-radius:6px; color:#1d4ed8; background:#fff; font-size:12px; }
.memory-row { padding:10px 0; border-bottom:1px solid #eef0f3; }.memory-row p { margin:0 0 6px; color:#374151; font-size:12px; line-height:1.5; }.memory-row small { color:#7b8490; }.memory-row div { display:flex; gap:6px; margin-top:8px; }.memory-row button { min-height:28px; padding:0 10px; border:1px solid #cfd6df; border-radius:5px; color:#374151; background:#fff; font-size:11px; }
</style>
