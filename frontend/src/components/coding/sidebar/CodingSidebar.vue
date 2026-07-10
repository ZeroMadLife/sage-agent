<script setup lang="ts">
import {
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  MessagesSquare,
  Plus,
  Search,
  Server,
  XCircle,
  Zap,
} from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { useCodingStore } from '../../../stores/coding'
import type { CodingSkillSummary } from '../../../types/api'

const store = useCodingStore()
const emit = defineEmits<{ useSkill: [name: string] }>()

// Collapsible panels: Sessions expanded by default, others collapsed.
const collapsedPanels = ref<Set<string>>(new Set(['skills', 'runs', 'mcp', 'memory']))

function togglePanel(panel: string) {
  const next = new Set(collapsedPanels.value)
  if (next.has(panel)) next.delete(panel)
  else next.add(panel)
  collapsedPanels.value = next
}

function isPanelCollapsed(panel: string) {
  return collapsedPanels.value.has(panel)
}

const SESSION_LIMIT = 10
const sessionQuery = ref('')
const showAllSessions = ref(false)

const filteredSessions = computed(() => {
  const query = sessionQuery.value.trim().toLowerCase()
  if (!query) return store.codingSessions
  return store.codingSessions.filter(
    (session) =>
      session.session_id.toLowerCase().includes(query) ||
      session.title.toLowerCase().includes(query),
  )
})

const visibleSessions = computed(() => {
  if (showAllSessions.value) return filteredSessions.value
  return filteredSessions.value.slice(0, SESSION_LIMIT)
})

const hiddenSessionCount = computed(
  () => Math.max(0, filteredSessions.value.length - SESSION_LIMIT),
)

function showAllSessionsNow() {
  showAllSessions.value = true
}

// Reset "show all" whenever the query changes so the limit reapplies.
watch(sessionQuery, () => {
  showAllSessions.value = false
})

const expandedSkill = ref<string | null>(null)
const skillQuery = ref('')
const collapsedSources = ref<Set<string>>(new Set())

const sourceOrder = ['builtin', 'user', 'project']
const groupedSkills = computed(() => {
  const query = skillQuery.value.trim().toLowerCase()
  return sourceOrder
    .map((source) => ({
      source,
      skills: store.skills.filter((skill) => {
        if (skill.source !== source) return false
        if (!query) return true
        return `${skill.name} ${skill.description}`.toLowerCase().includes(query)
      }),
    }))
    .filter((group) => group.skills.length > 0)
})

function toggleSkill(name: string) {
  expandedSkill.value = expandedSkill.value === name ? null : name
}

function useSkill(skill: CodingSkillSummary) {
  emit('useSkill', `/${skill.name}`)
  expandedSkill.value = null
}

function toggleSource(source: string) {
  const next = new Set(collapsedSources.value)
  if (next.has(source)) next.delete(source)
  else next.add(source)
  collapsedSources.value = next
}

function runIcon(status: string) {
  if (status === 'completed') return CheckCircle2
  if (status === 'error' || status === 'cancelled') return XCircle
  return Clock3
}
</script>

<template>
  <aside class="sidebar">
    <section class="sidebar-section">
      <div class="section-heading">
        <button class="panel-toggle" @click="togglePanel('sessions')">
          <component
            :is="isPanelCollapsed('sessions') ? ChevronRight : ChevronDown"
            :size="13"
          />
          <h3><MessagesSquare :size="13" /> Sessions</h3>
        </button>
        <button
          class="icon-action"
          data-testid="new-coding-session"
          title="New session"
          @click="store.startNewSession()"
        >
          <Plus :size="13" />
        </button>
      </div>
      <div v-if="!isPanelCollapsed('sessions')">
        <label v-if="store.codingSessions.length > 0" class="session-search">
          <Search :size="12" />
          <input
            v-model="sessionQuery"
            type="search"
            placeholder="Search sessions"
            aria-label="Search sessions"
          />
        </label>
        <div v-if="store.codingSessions.length === 0" class="empty">暂无 session</div>
        <button
          v-for="session in visibleSessions"
          :key="session.session_id"
          class="session-item"
          :class="{ active: session.session_id === store.sessionId }"
          @click="store.selectSession(session.session_id)"
        >
          <div class="session-title">{{ session.title }}</div>
          <div class="session-meta">
            {{ session.message_count }} messages · {{ session.runtime_mode }}
          </div>
        </button>
        <button
          v-if="!showAllSessions && hiddenSessionCount > 0"
          class="show-all-sessions"
          @click="showAllSessionsNow"
        >
          显示全部 ({{ filteredSessions.length }})
        </button>
      </div>
    </section>

    <section class="sidebar-section">
      <div class="section-heading">
        <button class="panel-toggle" @click="togglePanel('skills')">
          <component
            :is="isPanelCollapsed('skills') ? ChevronRight : ChevronDown"
            :size="13"
          />
          <h3><Zap :size="13" /> Skills</h3>
        </button>
      </div>

      <div v-if="!isPanelCollapsed('skills')">
        <label class="skill-search">
          <Search :size="12" />
          <input v-model="skillQuery" type="search" placeholder="Search skills" />
        </label>

        <div v-if="groupedSkills.length === 0" class="empty">暂无 skill</div>
        <div v-for="group in groupedSkills" :key="group.source" class="skill-group">
          <button class="source-header" @click="toggleSource(group.source)">
            <component
              :is="collapsedSources.has(group.source) ? ChevronRight : ChevronDown"
              :size="13"
            />
            <span>{{ group.source }}</span>
            <span>{{ group.skills.length }}</span>
          </button>

          <div v-if="!collapsedSources.has(group.source)">
            <div v-for="skill in group.skills" :key="skill.name" class="skill-item">
              <button class="skill-header" @click="toggleSkill(skill.name)">
                <component
                  :is="expandedSkill === skill.name ? ChevronDown : ChevronRight"
                  :size="13"
                />
                <span class="skill-name">/{{ skill.name }}</span>
                <span class="skill-source" :class="skill.source">{{ skill.source }}</span>
              </button>
              <div v-if="expandedSkill === skill.name" class="skill-detail">
                <p class="skill-desc">{{ skill.description }}</p>
                <button class="skill-use" @click="useSkill(skill)">
                  <Zap :size="12" /> 使用
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="sidebar-section">
      <div class="section-heading">
        <button class="panel-toggle" @click="togglePanel('memory')">
          <component
            :is="isPanelCollapsed('memory') ? ChevronRight : ChevronDown"
            :size="13"
          />
          <h3><Brain :size="13" /> 记忆</h3>
        </button>
      </div>
      <div v-if="!isPanelCollapsed('memory')">
        <p class="memory-hint">
          <code>/remember</code> 记住项目约定<br />
          <code>/dream</code> 整理记忆
        </p>
      </div>
    </section>

    <section class="sidebar-section">
      <div class="section-heading">
        <button class="panel-toggle" @click="togglePanel('runs')">
          <component
            :is="isPanelCollapsed('runs') ? ChevronRight : ChevronDown"
            :size="13"
          />
          <h3><Clock3 :size="13" /> Runs</h3>
        </button>
      </div>
      <div v-if="!isPanelCollapsed('runs')">
        <div v-if="store.runs.length === 0" class="empty">暂无 run</div>
        <div v-for="run in store.runs" :key="run.run_id" class="run-item">
          <button class="run-header" @click="store.loadRunDetail(run.run_id)">
            <component :is="runIcon(run.status)" :size="13" />
            <span class="run-id">{{ run.run_id.replace('run_', '') }}</span>
            <span class="run-status" :class="run.status">{{ run.status }}</span>
          </button>
          <div class="run-meta">
            {{ run.tool_count }} tools · {{ run.event_count }} events · {{ run.last_event_type }}
            <span v-if="run.changed_files && run.changed_files.length > 0">
              · {{ run.changed_files.length }} files changed
            </span>
          </div>
        </div>
        <div v-if="store.selectedRun" class="run-detail">
          <div class="run-detail-title">
            {{ store.selectedRun.run_id }}
            <span>{{ store.selectedRun.timeline?.length || store.selectedRun.events.length }} steps</span>
          </div>
          <div
            v-for="(entry, index) in store.selectedRun.timeline?.slice(-8) || []"
            :key="index"
            class="run-timeline-entry"
            :class="entry.status"
          >
            <span class="run-timeline-dot"></span>
            <span class="run-timeline-body">
              <span class="run-timeline-title">{{ entry.title }}</span>
              <span v-if="entry.detail" class="run-timeline-detail">{{ entry.detail }}</span>
            </span>
          </div>
          <div
            v-if="!store.selectedRun.timeline?.length"
            v-for="(event, index) in store.selectedRun.events.slice(-8)"
            :key="`event-${index}`"
            class="run-event"
          >
            {{ event.type }}
          </div>
        </div>
      </div>
    </section>

    <section class="sidebar-section">
      <div class="section-heading">
        <button class="panel-toggle" @click="togglePanel('mcp')">
          <component
            :is="isPanelCollapsed('mcp') ? ChevronRight : ChevronDown"
            :size="13"
          />
          <h3><Server :size="13" /> MCP</h3>
        </button>
      </div>
      <div v-if="!isPanelCollapsed('mcp')">
        <div v-if="store.mcpServers.length === 0" class="empty">暂无 MCP server</div>
        <div v-for="server in store.mcpServers" :key="server.name" class="mcp-item">
          <span class="mcp-dot" :class="server.status"></span>
          <span class="mcp-name">{{ server.name }}</span>
          <span class="mcp-transport">{{ server.transport }}</span>
        </div>
      </div>
    </section>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 0;
  height: 100%;
  overflow-y: auto;
  background: #fafbfc;
  border-right: 1px solid #e5e7eb;
}

.sidebar-section {
  padding: 12px 14px;
  border-bottom: 1px solid #f0f1f3;
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 0 8px;
}

.panel-toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: transparent;
  padding: 0;
  cursor: pointer;
  color: inherit;
}

.panel-toggle :deep(h3) {
  margin: 0;
}

.sidebar-section h3 {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  color: #6b7280;
  letter-spacing: 0.04em;
}

.sidebar-section > h3 {
  margin-bottom: 8px;
}

.icon-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  color: #374151;
  background: #ffffff;
  cursor: pointer;
}

.icon-action:hover {
  background: #f3f4f6;
}

.empty {
  color: #9ca3af;
  font-size: 12px;
}

.memory-hint {
  font-size: 12px;
  color: #6b7280;
  line-height: 1.6;
  padding: 4px 0;
}

.memory-hint code {
  background: #f3f4f6;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}

.session-item {
  display: block;
  width: 100%;
  padding: 5px 7px;
  border: 0;
  border-radius: 6px;
  margin-bottom: 4px;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.session-item.active {
  background: #eef6ff;
}

.session-item:hover {
  background: #f3f4f6;
}

.session-title {
  overflow: hidden;
  color: #111827;
  font-size: 12px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-meta {
  margin-top: 2px;
  color: #6b7280;
  font-size: 11px;
}

.skill-search {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 8px;
  padding: 4px 6px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fff;
  color: #6b7280;
}

.session-search {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 8px;
  padding: 4px 6px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fff;
  color: #6b7280;
}

.session-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  font-size: 12px;
}

.show-all-sessions {
  display: block;
  width: 100%;
  margin-top: 4px;
  padding: 4px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #2563eb;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  text-align: center;
}

.show-all-sessions:hover {
  background: #eff6ff;
}

.skill-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  font-size: 12px;
}

.source-header {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  margin: 4px 0 2px;
  border: 0;
  background: transparent;
  padding: 3px 0;
  color: #6b7280;
  cursor: pointer;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

.source-header span:nth-child(2) {
  flex: 1;
  text-align: left;
}

.skill-item {
  margin-bottom: 2px;
}

.skill-header {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  border: 0;
  background: transparent;
  padding: 4px 0;
  cursor: pointer;
  text-align: left;
}

.skill-name {
  font-size: 13px;
  font-weight: 600;
  color: #111827;
  flex: 1;
}

.skill-source {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 3px;
  background: #e5e7eb;
  color: #4b5563;
}

.skill-source.builtin {
  background: #dbeafe;
  color: #1e40af;
}

.skill-source.project {
  background: #d1fae5;
  color: #065f46;
}

.skill-source.user {
  background: #fef3c7;
  color: #92400e;
}

.skill-detail {
  padding: 4px 0 6px 18px;
}

.skill-desc {
  margin: 0 0 6px;
  font-size: 12px;
  color: #4b5563;
}

.skill-use {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  background: #fff;
  padding: 2px 8px;
  font-size: 11px;
  cursor: pointer;
}

.skill-use:hover {
  background: #f3f4f6;
}

.run-item {
  margin-bottom: 6px;
}

.run-header {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  border: 0;
  background: transparent;
  padding: 3px 0;
  cursor: pointer;
  text-align: left;
}

.run-id {
  flex: 1;
  overflow: hidden;
  color: #111827;
  font-size: 12px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-status {
  padding: 1px 5px;
  border-radius: 3px;
  background: #e5e7eb;
  color: #4b5563;
  font-size: 10px;
  font-weight: 700;
}

.run-status.completed {
  background: #d1fae5;
  color: #065f46;
}

.run-status.cancelled,
.run-status.error {
  background: #fee2e2;
  color: #991b1b;
}

.run-meta {
  padding-left: 18px;
  color: #6b7280;
  font-size: 11px;
}

.run-detail {
  margin-top: 8px;
  padding: 6px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fff;
}

.run-detail-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
  color: #374151;
  font-size: 11px;
  font-weight: 700;
}

.run-detail-title span {
  color: #9ca3af;
  font-weight: 600;
}

.run-timeline-entry {
  display: grid;
  grid-template-columns: 8px 1fr;
  gap: 6px;
  padding: 5px 0;
}

.run-timeline-dot {
  width: 7px;
  height: 7px;
  margin-top: 4px;
  border-radius: 50%;
  background: #9ca3af;
}

.run-timeline-entry.done .run-timeline-dot {
  background: #10b981;
}

.run-timeline-entry.running .run-timeline-dot {
  background: #3b82f6;
}

.run-timeline-entry.blocked .run-timeline-dot {
  background: #f59e0b;
}

.run-timeline-entry.error .run-timeline-dot {
  background: #ef4444;
}

.run-timeline-body {
  min-width: 0;
}

.run-timeline-title {
  display: block;
  overflow: hidden;
  color: #374151;
  font-size: 11px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-timeline-detail {
  display: -webkit-box;
  overflow: hidden;
  color: #6b7280;
  font-size: 11px;
  line-height: 1.35;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.run-event {
  color: #6b7280;
  font-family: 'SF Mono', monospace;
  font-size: 11px;
}

.mcp-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
  font-size: 12px;
}

.mcp-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #10b981;
}

.mcp-dot.configured {
  background: #6b7280;
}

.mcp-name {
  flex: 1;
  font-weight: 500;
  color: #374151;
}

.mcp-transport {
  font-size: 10px;
  color: #9ca3af;
}
</style>
