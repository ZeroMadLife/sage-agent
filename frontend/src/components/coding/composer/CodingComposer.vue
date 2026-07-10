<script setup lang="ts">
import { computed, ref } from 'vue'
import { Send, Square } from 'lucide-vue-next'
import { useCodingStore } from '../../../stores/coding'
import type { CodingSkillSummary } from '../../../types/api'
import CodingPermissionModeDrawer from './CodingPermissionModeDrawer.vue'

const store = useCodingStore()
const input = ref('')

const canSend = computed(
  () => Boolean(input.value.trim()) && Boolean(store.sessionId) && !store.isThinking,
)

const contextColor = computed(() => {
  const pct = store.contextPercent
  if (pct > 80) return '#ef4444'
  if (pct > 60) return '#f59e0b'
  return '#10b981'
})

const circumference = 2 * Math.PI * 14
const dashOffset = computed(
  () => circumference - (store.contextPercent / 100) * circumference,
)
const contextTooltip = computed(
  () =>
    `${store.contextChars} chars used / ${store.contextBudget} budget (${store.contextPercent}%)`,
)

// --- Skill menu (triggered when input starts with "/") ---
const skillMenuDismissed = ref(false)
const showSkillMenu = computed(
  () => input.value.startsWith('/') && !skillMenuDismissed.value,
)

const skillQuery = computed(() => input.value.slice(1).toLowerCase())

const filteredSkills = computed<CodingSkillSummary[]>(() => {
  const query = skillQuery.value.trim()
  if (!query) return store.skills
  return store.skills.filter((skill) =>
    `${skill.name} ${skill.description}`.toLowerCase().includes(query),
  )
})

const selectedIndex = ref(0)

function selectSkill(skill: CodingSkillSummary) {
  input.value = `/${skill.name} `
  selectedIndex.value = 0
  skillMenuDismissed.value = true
}

function onInput() {
  // Reset selection whenever the filter result set changes.
  selectedIndex.value = 0
  if (!input.value.startsWith('/')) {
    skillMenuDismissed.value = false
  }
}

function send() {
  const content = input.value.trim()
  if (!content) return
  store.sendMessage(content)
  input.value = ''
  selectedIndex.value = 0
  skillMenuDismissed.value = false
}

function stop() {
  void store.stopCurrentRun()
}

function onKeydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  if (showSkillMenu.value && filteredSkills.value.length > 0) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      selectedIndex.value = (selectedIndex.value + 1) % filteredSkills.value.length
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      const count = filteredSkills.value.length
      selectedIndex.value = (selectedIndex.value - 1 + count) % count
      return
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      const skills = filteredSkills.value
      // Clamp in case the filter narrowed after the selection was set.
      const skill = skills[Math.min(selectedIndex.value, skills.length - 1)]
      if (skill) selectSkill(skill)
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      input.value = ''
      selectedIndex.value = 0
      skillMenuDismissed.value = false
      return
    }
  }
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    send()
  }
}

defineExpose({
  setInput(value: string) {
    input.value = value
  },
})
</script>

<template>
  <div class="composer">
    <div class="composer-controls">
      <button
        v-if="store.contextPercent > 75"
        class="compact-hint"
        type="button"
        :title="contextTooltip"
      >
        压缩
      </button>

      <CodingPermissionModeDrawer />

      <div class="context-ring" :title="contextTooltip">
        <svg width="32" height="32" viewBox="0 0 32 32">
          <circle cx="16" cy="16" r="14" fill="none" stroke="#e5e7eb" stroke-width="3" />
          <circle
            cx="16"
            cy="16"
            r="14"
            fill="none"
            :stroke="contextColor"
            stroke-width="3"
            :stroke-dasharray="circumference"
            :stroke-dashoffset="dashOffset"
            transform="rotate(-90 16 16)"
            stroke-linecap="round"
          />
        </svg>
        <span class="context-pct">{{ store.contextPercent }}%</span>
      </div>
    </div>

    <div class="composer-input">
      <div class="composer-textarea-wrap">
        <textarea
          v-model="input"
          rows="2"
          :disabled="!store.sessionId || store.isThinking"
          placeholder="输入任务，或 /review 调用 skill"
          @keydown="onKeydown"
          @input="onInput"
        />
        <ul v-if="showSkillMenu && filteredSkills.length > 0" class="skill-menu" role="listbox">
          <li
            v-for="(skill, index) in filteredSkills"
            :key="skill.name"
            class="skill-menu-item"
            :class="{ active: index === selectedIndex }"
            role="option"
            :aria-selected="index === selectedIndex"
            @mouseenter="selectedIndex = index"
            @click="selectSkill(skill)"
          >
            <span class="skill-menu-name">/{{ skill.name }}</span>
            <span class="skill-menu-desc">{{ skill.description }}</span>
          </li>
        </ul>
        <p v-else-if="showSkillMenu && filteredSkills.length === 0" class="skill-menu-empty">
          无匹配 skill
        </p>
      </div>
      <button
        v-if="store.isThinking"
        class="stop-btn"
        type="button"
        title="Stop current run"
        @click="stop"
      >
        <Square :size="13" />
      </button>
      <button v-else :disabled="!canSend" class="send-btn" @click="send">
        <Send :size="15" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.composer {
  border-top: 1px solid #e5e7eb;
  background: #fff;
  padding: 10px 16px;
}

.composer-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.context-ring {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-left: auto;
}

.compact-hint {
  margin-left: auto;
  border: 1px solid #f59e0b;
  border-radius: 6px;
  background: #fffbeb;
  color: #92400e;
  padding: 3px 7px;
  font-size: 11px;
  font-weight: 700;
}

.compact-hint + .context-ring {
  margin-left: 0;
}

.context-pct {
  position: absolute;
  font-size: 9px;
  font-weight: 700;
  color: #4b5563;
}

.composer-input {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

.composer-textarea-wrap {
  position: relative;
  flex: 1;
}

.composer-input textarea {
  width: 100%;
  resize: vertical;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  padding: 8px 10px;
  line-height: 1.5;
  font-size: 13px;
  font-family: inherit;
}

.composer-input textarea:focus {
  outline: none;
  border-color: #3b82f6;
}

.skill-menu {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 100%;
  margin: 0 0 4px;
  padding: 4px;
  list-style: none;
  max-height: 240px;
  overflow-y: auto;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.16);
  z-index: 10;
}

.skill-menu-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
}

.skill-menu-item.active {
  background: #eff6ff;
}

.skill-menu-name {
  font-size: 13px;
  font-weight: 700;
  color: #111827;
  white-space: nowrap;
}

.skill-menu-desc {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: #6b7280;
}

.skill-menu-empty {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 100%;
  margin: 0 0 4px;
  padding: 6px 10px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.16);
  font-size: 12px;
  color: #9ca3af;
  z-index: 10;
}

.send-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 0;
  border-radius: 8px;
  background: #111827;
  color: #fff;
  cursor: pointer;
}

.stop-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid #ef4444;
  border-radius: 8px;
  background: #fff;
  color: #dc2626;
  cursor: pointer;
}

.send-btn:disabled {
  background: #d1d5db;
  cursor: not-allowed;
}
</style>
