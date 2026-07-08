<script setup lang="ts">
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  FileCode,
  MessagesSquare,
  Search,
  Server,
  XCircle,
  Zap,
} from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { useCodingStore } from '../stores/coding'
import type { CodingSkillSummary } from '../types/api'

const store = useCodingStore()
const emit = defineEmits<{ useSkill: [name: string] }>()

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
      <h3><MessagesSquare :size="13" /> Sessions</h3>
      <div v-if="store.codingSessions.length === 0" class="empty">暂无 session</div>
      <button
        v-for="session in store.codingSessions"
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
    </section>

    <section class="sidebar-section">
      <h3>Skills</h3>

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
    </section>

    <section class="sidebar-section">
      <h3><Clock3 :size="13" /> Runs</h3>
      <div v-if="store.runs.length === 0" class="empty">暂无 run</div>
      <div v-for="run in store.runs" :key="run.run_id" class="run-item">
        <button class="run-header" @click="store.loadRunDetail(run.run_id)">
          <component :is="runIcon(run.status)" :size="13" />
          <span class="run-id">{{ run.run_id.replace('run_', '') }}</span>
          <span class="run-status" :class="run.status">{{ run.status }}</span>
        </button>
        <div class="run-meta">
          {{ run.tool_count }} tools · {{ run.event_count }} events · {{ run.last_event_type }}
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
    </section>

    <section class="sidebar-section">
      <h3><Server :size="13" /> MCP</h3>
      <div v-if="store.mcpServers.length === 0" class="empty">暂无 MCP server</div>
      <div v-for="server in store.mcpServers" :key="server.name" class="mcp-item">
        <span class="mcp-dot" :class="server.status"></span>
        <span class="mcp-name">{{ server.name }}</span>
        <span class="mcp-transport">{{ server.transport }}</span>
      </div>
    </section>

    <section class="sidebar-section">
      <h3><FileCode :size="13" /> 模型</h3>
      <div v-if="store.models.length === 0" class="empty">加载中...</div>
      <div
        v-for="model in store.models"
        :key="model.id"
        class="model-item"
        :class="{ active: model.id === store.currentModelId }"
        @click="store.changeModel(model.id)"
      >
        <span class="model-provider">{{ model.provider }}</span>
        <span class="model-label">{{ model.label.split(' / ')[1] || model.label }}</span>
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

.sidebar-section h3 {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0 0 8px;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  color: #6b7280;
  letter-spacing: 0.04em;
}

.empty {
  color: #9ca3af;
  font-size: 12px;
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

.model-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
}

.model-item:hover {
  background: #f3f4f6;
}

.model-item.active {
  background: #dbeafe;
}

.model-provider {
  font-size: 10px;
  font-weight: 700;
  color: #6b7280;
  text-transform: uppercase;
}

.model-label {
  color: #374151;
}
</style>
