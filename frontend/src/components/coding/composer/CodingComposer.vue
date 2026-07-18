<script setup lang="ts">
import { computed, ref } from 'vue'
import { BrainCircuit, Send, Settings, Square } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import { useCodingStore } from '../../../stores/coding'
import type { CodingSkillSummary } from '../../../types/api'
import type { HarnessSurfaceContext } from '../../../harness/types'
import CodingContextBudget from '../chat/CodingContextBudget.vue'
import CodingPermissionModeDrawer from './CodingPermissionModeDrawer.vue'

const props = withDefaults(defineProps<{
  density?: 'default' | 'compact'
  surfaceContext?: HarnessSurfaceContext | null
  placeholder?: string
}>(), {
  density: 'default',
  surfaceContext: null,
  placeholder: '输入任务，或 /review 调用 skill',
})

const store = useCodingStore()
const router = useRouter()
const input = ref('')
const currentModel = computed(() => store.models.find((model) => model.id === store.currentModelId))
const reasoningModes = computed(() => currentModel.value?.reasoning_modes ?? [])
const reasoningOptions = computed<Array<'off' | 'low' | 'medium' | 'high'>>(
  () => ['off', ...reasoningModes.value],
)

const canSend = computed(
  () => Boolean(input.value.trim()) && Boolean(store.sessionId) && !store.isThinking,
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
  if (props.surfaceContext) store.sendMessage(content, props.surfaceContext)
  else store.sendMessage(content)
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

function openSettings() {
  void router.push('/settings/providers')
}

async function onReasoningChange(mode: 'off' | 'low' | 'medium' | 'high') {
  await store.changeReasoning(mode)
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
  <div class="composer" :class="props.density">
    <div class="composer-frame">
      <div class="composer-input">
        <div class="composer-textarea-wrap">
          <div class="composer-meta"><CodingContextBudget /></div>
          <textarea
            v-model="input"
            rows="2"
            :disabled="!store.sessionId || store.isThinking"
            :placeholder="placeholder"
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
      </div>
      <div class="composer-controls">
        <button class="rail-icon" type="button" title="打开 Provider 设置" aria-label="打开 Provider 设置" @click="openSettings"><Settings :size="14" /></button>
        <CodingPermissionModeDrawer />
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
        <div v-if="reasoningModes.length" class="reasoning-control" aria-label="推理强度">
          <BrainCircuit :size="13" aria-hidden="true" />
          <button
            v-for="mode in reasoningOptions"
            :key="mode"
            type="button"
            :class="{ active: store.reasoningMode === mode }"
            :aria-pressed="store.reasoningMode === mode"
            :disabled="store.isThinking"
            @click="onReasoningChange(mode)"
          >{{ mode === 'off' ? '关' : mode }}</button>
        </div>
        <span v-if="store.compactionError" class="context-error" role="alert" aria-live="polite">{{ store.compactionError }}</span>
        <button v-if="store.isThinking" class="stop-btn" type="button" title="停止当前运行" aria-label="停止当前运行" @click="stop"><Square :size="13" /></button>
        <button v-else :disabled="!canSend" class="send-btn" type="button" title="发送任务" aria-label="发送任务" @click="send"><Send :size="15" /></button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.composer {
  position: relative;
  background: var(--sage-surface);
  padding: 0 clamp(18px, 4vw, 56px) max(12px, env(safe-area-inset-bottom));
}

.composer::before { position:absolute; inset:0 0 auto; height:24px; background:linear-gradient(to bottom,transparent,var(--sage-surface)); transform:translateY(-100%); pointer-events:none; content:''; }

.composer-frame {
  view-transition-name: sage-composer;
  position: relative;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--sage-border-strong);
  border-radius: var(--sage-radius-lg);
  background: var(--sage-surface);
  overflow: visible;
  width: 100%;
  max-width: var(--chat-content-max, 1120px);
  margin: 0 auto;
}

.composer-frame:focus-within {
  border-color: var(--sage-focus);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--sage-focus) 14%, transparent);
}

.composer.compact {
  padding-right: 10px;
  padding-left: 10px;
  padding-bottom: 10px;
}

.composer.compact .composer-frame { border-radius: var(--sage-radius); }
.composer.compact .composer-input textarea {
  min-height: 58px;
  max-height: 160px;
  padding-top: 11px;
  padding-bottom: 7px;
  border-radius: var(--sage-radius) var(--sage-radius) 0 0;
  resize: none;
  line-height: 1.5;
}
.composer.compact .composer-controls {
  gap: 4px;
  min-height: 36px;
  padding: 0 6px 6px;
}
.composer.compact .model-select { max-width: 126px; padding-right: 4px; padding-left: 4px; }
.composer.compact .composer-meta { top: 8px; right: 9px; max-width: 132px; }
.composer.compact .composer-meta :deep(.context-copy-desktop) { display: none; }
.composer.compact .composer-meta :deep(.context-copy-mobile) { display: block; }
.composer.compact .composer-meta :deep(.context-summary) { grid-template-columns: minmax(0, 1fr) 28px; width: 116px; }
.composer.compact .composer-meta :deep(.context-track) { width: 28px; }
.composer.compact .composer-meta :deep(.compact-context) { display: none; }

.composer-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  order: 2;
  z-index: 2;
  min-height: 38px;
  margin: 0;
  padding: 0 8px 8px 8px;
  border-top: 1px solid color-mix(in srgb, var(--sage-border) 78%, transparent);
}

.model-select {
  border: 0;
  border-radius: var(--sage-radius);
  padding: 3px 8px;
  font-size: var(--sage-font-xs);
  background: transparent;
  color: var(--sage-text-secondary);
  cursor: pointer;
  max-width: 160px;
}

.rail-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--sage-radius);
  color: var(--sage-text-muted);
  background: transparent;
}

.rail-icon:hover { border-color: var(--sage-border); color: var(--sage-text); background: var(--sage-surface-muted); }

.reasoning-control { display:flex; align-items:center; gap:2px; min-height:28px; padding:2px 3px 2px 6px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-muted); }
.reasoning-control button { min-width:30px; height:24px; padding:0 5px; border:0; border-radius:var(--sage-radius-sm); color:var(--sage-text-muted); background:transparent; font-size:var(--sage-font-xs); }
.reasoning-control button.active { color:var(--sage-text); background:var(--sage-surface-muted); font-weight:700; }

.model-select:focus {
  outline: none;
  border-color: var(--sage-focus);
}

.context-error {
  color: var(--sage-danger);
  font-size: var(--sage-font-xs);
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-input {
  position: static;
  order: 1;
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

.composer-meta { position:absolute; z-index:2; top:10px; right:12px; max-width:min(54%, 330px); }

.composer-textarea-wrap {
  position: relative;
  flex: 1;
}

.composer-input textarea {
  width: 100%;
  resize: vertical;
  min-height: 74px;
  max-height: 240px;
  border: 0;
  border-radius: var(--sage-radius-lg) var(--sage-radius-lg) 0 0;
  color: var(--sage-text);
  background: var(--sage-surface);
  padding: 14px min(42%, 330px) 9px 14px;
  line-height: 1.65;
  font-size: var(--sage-font-body);
  font-family: inherit;
}

.composer-input textarea:focus {
  outline: none;
  box-shadow: none;
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
  position: static;
  margin-left: auto;
  width: 32px;
  height: 32px;
  border: 1px solid var(--sage-border-strong);
  border-radius: 50%;
  background: var(--sage-text-secondary);
  color: var(--sage-surface);
  cursor: pointer;
  z-index: 3;
}

.stop-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  position: static;
  margin-left: auto;
  width: 32px;
  height: 32px;
  border: 1px solid var(--sage-danger);
  border-radius: 50%;
  background: var(--sage-surface);
  color: var(--sage-danger);
  cursor: pointer;
  z-index: 3;
}

.send-btn:disabled {
  border-color: var(--sage-border);
  background: var(--sage-surface-muted);
  color: var(--sage-text-muted);
  cursor: not-allowed;
}

@media (max-width: 720px) {
  .composer { padding-right:12px; padding-left:12px; }
  .composer.compact .composer-controls {
    display:grid;
    grid-template-columns:28px auto minmax(0,1fr) 32px;
    grid-template-areas:
      "settings permission model send"
      "reasoning reasoning reasoning reasoning";
    gap:4px;
    overflow:visible;
  }
  .composer.compact .rail-icon { grid-area:settings; }
  .composer.compact :deep(.permission-mode-control) { grid-area:permission; min-width:0; }
  .composer.compact .model-select { grid-area:model; width:100%; max-width:none; min-width:0; }
  .composer.compact .reasoning-control { grid-area:reasoning; justify-content:stretch; min-width:0; padding-left:3px; }
  .composer.compact .reasoning-control button { flex:1; min-width:0; }
  .composer.compact .send-btn,.composer.compact .stop-btn { grid-area:send; margin-left:0; }
  .composer.compact .context-error { grid-column:1 / -1; }
  .composer-input textarea { padding-right:128px; }
  .reasoning-control svg { display:none; }
}
</style>
