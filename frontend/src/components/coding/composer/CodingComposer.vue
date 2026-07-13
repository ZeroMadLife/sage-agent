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

const contextTooltip = computed(
  () =>
    store.contextConfigured
      ? `上下文 ${store.contextChars.toLocaleString()} / ${store.contextBudget.toLocaleString()} tokens (${store.contextPercent}%)`
      : '当前模型未配置上下文窗口',
)
const contextStatus = computed(() => {
  if (!store.contextConfigured) return { label: '模型未配置', tone: 'muted' }
  if (store.contextBusy) return { label: '正在压缩', tone: 'running' }
  if (store.compactionError) return { label: '压缩失败', tone: 'danger' }
  if (store.contextSnapshot?.level === 'emergency') return { label: '接近上限', tone: 'danger' }
  if (store.contextSnapshot?.level === 'high' || store.contextSnapshot?.level === 'compact') {
    return { label: '即将压缩', tone: 'warning' }
  }
  return { label: '正常', tone: 'success' }
})

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

async function onModelChange(modelId: string) {
  // Don't use v-model: only update currentModelId on success to avoid
  // UI showing a model that failed to switch on the server.
  await store.changeModel(modelId)
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
      <select
        v-if="store.models.length > 0"
        :value="store.currentModelId"
        class="model-select"
        title="切换模型"
        :disabled="store.isThinking"
        @change="onModelChange(($event.target as HTMLSelectElement).value)"
      >
        <option v-for="model in store.models" :key="model.id" :value="model.id">
          {{ model.label }}
        </option>
      </select>

      <button
        v-if="store.contextConfigured"
        class="compact-hint"
        type="button"
        :title="contextTooltip"
        :disabled="store.isThinking || store.contextBusy || !store.contextCompactable"
        :aria-busy="store.contextBusy"
        @click="store.compactContext()"
      >
        {{ store.contextBusy ? '压缩中…' : '压缩' }}
      </button>

      <CodingPermissionModeDrawer />

      <div
        class="context-budget"
        :title="contextTooltip"
        role="progressbar"
        :aria-label="contextTooltip"
        :aria-valuenow="store.contextPercent"
        aria-valuemin="0"
        aria-valuemax="100"
      >
        <div class="context-budget-copy">
          <span class="context-budget-label">上下文</span>
          <strong v-if="store.contextConfigured">{{ store.contextChars.toLocaleString() }} / {{ store.contextBudget.toLocaleString() }}</strong>
          <strong v-else>--</strong>
        </div>
        <span class="context-budget-status" :class="contextStatus.tone">{{ contextStatus.label }}</span>
        <span class="context-budget-meter" aria-hidden="true"><span :class="contextStatus.tone" :style="{ width: `${store.contextPercent}%` }"></span></span>
      </div>
      <span v-if="store.compactionError" class="context-error" role="alert" aria-live="polite">
        {{ store.compactionError }}
      </span>
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
        title="停止当前运行"
        aria-label="停止当前运行"
        @click="stop"
      >
        <Square :size="13" />
      </button>
      <button v-else :disabled="!canSend" class="send-btn" type="button" title="发送任务" aria-label="发送任务" @click="send">
        <Send :size="15" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.composer {
  border-top: 1px solid var(--sage-border);
  background: var(--sage-surface);
  padding: 9px max(16px, calc((100% - 880px) / 2));
}

.composer-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.model-select {
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  padding: 3px 8px;
  font-size: 12px;
  background: var(--sage-surface);
  color: var(--sage-text-secondary);
  cursor: pointer;
  max-width: 160px;
}

.model-select:focus {
  outline: none;
  border-color: var(--sage-focus);
}

.compact-hint:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.context-error {
  color: var(--sage-danger);
  font-size: 11px;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.context-budget {
  display: grid;
  grid-template-columns: auto auto;
  align-items: center;
  column-gap: 7px;
  min-width: 170px;
  padding: 3px 0;
  color: var(--sage-text-secondary);
}

.context-budget-copy {
  display: flex;
  margin-left: auto;
  align-items: baseline;
  gap: 4px;
  font-size: 11px;
}

.context-budget-label { color: var(--sage-text-muted); }
.context-budget-copy strong { color: var(--sage-text-secondary); font-size: 11px; font-weight: 650; }

.context-budget-status { justify-self: end; font-size: 10px; font-weight: 700; }
.context-budget-status.success { color: var(--sage-success); }.context-budget-status.warning { color: var(--sage-warning); }.context-budget-status.danger { color: var(--sage-danger); }.context-budget-status.running,.context-budget-status.muted { color: var(--sage-text-muted); }

.context-budget-meter { grid-column: 1 / -1; display:block; width:100%; height:3px; margin-top:4px; overflow:hidden; border-radius:999px; background:var(--sage-surface-muted); }.context-budget-meter span { display:block; height:100%; border-radius:inherit; background:var(--sage-success); transition:width .2s ease; }.context-budget-meter span.warning { background:var(--sage-warning); }.context-budget-meter span.danger { background:var(--sage-danger); }.context-budget-meter span.running,.context-budget-meter span.muted { background:var(--sage-text-muted); }

.compact-hint {
  margin-left: auto;
  border: 1px solid color-mix(in srgb, var(--sage-warning) 55%, var(--sage-border));
  border-radius: var(--sage-radius);
  background: var(--sage-warning-bg);
  color: var(--sage-warning);
  padding: 3px 7px;
  font-size: 11px;
  font-weight: 700;
}

.compact-hint + .context-budget {
  margin-left: 0;
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
  min-height: 42px;
  max-height: 180px;
  border: 1px solid var(--sage-border-strong);
  border-radius: var(--sage-radius-lg);
  color: var(--sage-text);
  background: var(--sage-surface);
  padding: 8px 10px;
  line-height: 1.5;
  font-size: 13px;
  font-family: inherit;
}

.composer-input textarea:focus {
  outline: none;
  border-color: var(--sage-focus);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--sage-focus) 16%, transparent);
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
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius-lg);
  background: var(--sage-surface);
  box-shadow: var(--sage-shadow-drawer);
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
  background: var(--sage-surface-muted);
}

.skill-menu-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--sage-text);
  white-space: nowrap;
}

.skill-menu-desc {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--sage-text-muted);
}

.skill-menu-empty {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 100%;
  margin: 0 0 4px;
  padding: 6px 10px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius-lg);
  background: var(--sage-surface);
  box-shadow: var(--sage-shadow-drawer);
  font-size: 12px;
  color: var(--sage-text-muted);
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
  background: var(--sage-text);
  color: #fff;
  cursor: pointer;
}

.stop-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--sage-danger);
  border-radius: var(--sage-radius-lg);
  background: var(--sage-surface);
  color: var(--sage-danger);
  cursor: pointer;
}

.send-btn:disabled {
  background: var(--sage-border-strong);
  cursor: not-allowed;
}
</style>
