<script setup lang="ts">
import {
  Check,
  Cloud,
  FolderOpen,
  KeyRound,
  Laptop,
  LogOut,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type {
  DesktopOnboardingAction,
  DesktopOnboardingSnapshot,
} from '../../desktop/hostAdapter'

const props = defineProps<{
  snapshot: DesktopOnboardingSnapshot
  busy: boolean
  error: { reason_code: string, action: string } | null
}>()
const emit = defineEmits<{
  action: [action: DesktopOnboardingAction]
}>()

const workspacePath = ref('')
const providerForm = ref({
  name: '',
  base_url: 'https://api.openai.com/v1',
  api_key: '',
  default_model: '',
})
const addFormOpen = ref(false)
const rotatingProvider = ref('')
const rotationKey = ref('')
const deleteCandidate = ref('')

const showProviderForm = computed(() => (
  props.snapshot.stage === 'configure_provider' || addFormOpen.value
))
const canSaveProvider = computed(() => Boolean(
  providerForm.value.name.trim()
  && providerForm.value.base_url.trim()
  && providerForm.value.api_key.trim()
  && providerForm.value.default_model.trim(),
))

function chooseMode(mode: 'local' | 'cloud') {
  emit('action', { kind: 'choose_mode', mode })
}

function selectWorkspace() {
  const path = workspacePath.value.trim()
  if (!path) return
  emit('action', { kind: 'select_workspace', workspace_path: path })
}

function saveProvider() {
  if (!canSaveProvider.value || props.busy) return
  const action: DesktopOnboardingAction = {
    kind: 'add_provider',
    name: providerForm.value.name.trim(),
    base_url: providerForm.value.base_url.trim(),
    api_key: providerForm.value.api_key,
    default_model: providerForm.value.default_model.trim(),
  }
  emit('action', action)
  providerForm.value.api_key = ''
}

function setDefaultModel(providerId: string, event: Event) {
  const modelId = (event.target as HTMLSelectElement).value
  emit('action', { kind: 'set_default_model', provider_id: providerId, model_id: modelId })
}

function beginRotation(providerId: string) {
  rotatingProvider.value = providerId
  rotationKey.value = ''
}

function cancelRotation() {
  rotationKey.value = ''
  rotatingProvider.value = ''
}

function confirmRotation(providerId: string) {
  if (!rotationKey.value.trim()) return
  emit('action', {
    kind: 'rotate_provider_key',
    provider_id: providerId,
    api_key: rotationKey.value,
  })
  cancelRotation()
}

function confirmDelete(providerId: string) {
  emit('action', { kind: 'delete_provider', provider_id: providerId })
  deleteCandidate.value = ''
}
</script>

<template>
  <section class="onboarding" aria-label="首次启动设置">
    <div v-if="snapshot.stage === 'choose_mode'" class="onboarding-step">
      <h2>选择运行方式</h2>
      <div class="mode-control" role="group" aria-label="运行方式">
        <button type="button" data-mode="local" :disabled="busy" @click="chooseMode('local')">
          <Laptop :size="18" aria-hidden="true" />
          <span><strong>Local</strong><small>本机数据与 Provider</small></span>
        </button>
        <button type="button" data-mode="cloud" :disabled="busy" @click="chooseMode('cloud')">
          <Cloud :size="18" aria-hidden="true" />
          <span><strong>Cloud</strong><small>需要桌面 OAuth</small></span>
        </button>
      </div>
    </div>

    <div v-else-if="snapshot.stage === 'cloud_unavailable'" class="onboarding-step">
      <h2>Cloud 暂不可用</h2>
      <div class="inline-problem">
        <code>cloud_oauth_not_available</code>
        <button type="button" :disabled="busy" @click="chooseMode('local')">
          <Laptop :size="16" aria-hidden="true" /> 使用 Local
        </button>
      </div>
    </div>

    <form v-else-if="snapshot.stage === 'select_workspace'" class="onboarding-step" @submit.prevent="selectWorkspace">
      <h2>选择学习空间</h2>
      <label class="field wide">
        <span>Workspace 路径</span>
        <div class="input-with-icon">
          <FolderOpen :size="16" aria-hidden="true" />
          <input v-model="workspacePath" aria-label="Workspace 路径" autocomplete="off" />
        </div>
      </label>
      <button class="primary-command" type="submit" :disabled="busy || !workspacePath.trim()">
        <Check :size="16" aria-hidden="true" /> 继续
      </button>
    </form>

    <div v-else class="provider-section">
      <header class="section-heading">
        <div>
          <h2>Local Provider</h2>
          <small v-if="snapshot.workspace_name">{{ snapshot.workspace_name }}</small>
        </div>
        <button
          v-if="snapshot.stage === 'complete' && !showProviderForm"
          type="button"
          title="添加 Provider"
          aria-label="添加 Provider"
          :disabled="busy"
          @click="addFormOpen = true"
        >
          <Plus :size="17" aria-hidden="true" />
        </button>
      </header>

      <div v-if="snapshot.providers.length" class="provider-list">
        <article v-for="provider in snapshot.providers" :key="provider.provider_id" class="provider-item">
          <div class="provider-summary">
            <span class="status-dot" :data-status="provider.status" aria-hidden="true" />
            <div>
              <strong>{{ provider.name }}</strong>
              <small>{{ provider.key_configured ? provider.key_hint : '凭据已注销' }}</small>
            </div>
            <code>{{ provider.status }}</code>
          </div>

          <div class="provider-controls">
            <select
              v-if="provider.models.length"
              :value="provider.default_model || ''"
              aria-label="默认模型"
              :disabled="busy"
              @change="setDefaultModel(provider.provider_id, $event)"
            >
              <option v-for="model in provider.models" :key="model" :value="model">{{ model }}</option>
            </select>
            <button type="button" title="探测 Provider" aria-label="探测 Provider" :disabled="busy || !provider.key_configured" @click="emit('action', { kind: 'probe_provider', provider_id: provider.provider_id })">
              <RefreshCw :size="16" aria-hidden="true" />
            </button>
            <button type="button" title="轮换 API Key" aria-label="轮换 API Key" :disabled="busy" @click="beginRotation(provider.provider_id)">
              <KeyRound :size="16" aria-hidden="true" />
            </button>
            <button type="button" title="注销 Provider 凭据" aria-label="注销 Provider 凭据" :disabled="busy || !provider.key_configured" @click="emit('action', { kind: 'disconnect_provider', provider_id: provider.provider_id })">
              <LogOut :size="16" aria-hidden="true" />
            </button>
            <button type="button" title="删除 Provider" aria-label="删除 Provider" :disabled="busy" @click="deleteCandidate = provider.provider_id">
              <Trash2 :size="16" aria-hidden="true" />
            </button>
          </div>

          <form v-if="rotatingProvider === provider.provider_id" class="inline-editor" @submit.prevent="confirmRotation(provider.provider_id)">
            <input v-model="rotationKey" type="password" aria-label="新 API Key" autocomplete="new-password" />
            <button type="button" data-action="confirm-rotate" title="确认轮换" aria-label="确认轮换" :disabled="busy || !rotationKey.trim()" @click="confirmRotation(provider.provider_id)"><Check :size="16" /></button>
            <button type="button" title="取消轮换" aria-label="取消轮换" @click="cancelRotation"><X :size="16" /></button>
          </form>

          <div v-if="deleteCandidate === provider.provider_id" class="delete-confirm" role="alert">
            <span>删除 Provider 与 Keychain 凭据？</span>
            <button type="button" data-action="confirm-delete" :disabled="busy" @click="confirmDelete(provider.provider_id)">删除</button>
            <button type="button" @click="deleteCandidate = ''">取消</button>
          </div>
        </article>
      </div>

      <form v-if="showProviderForm" class="provider-form" @submit.prevent="saveProvider">
        <label class="field"><span>名称</span><input v-model="providerForm.name" aria-label="Provider 名称" autocomplete="off" /></label>
        <label class="field"><span>Base URL</span><input v-model="providerForm.base_url" aria-label="Base URL" autocomplete="url" /></label>
        <label class="field"><span>API Key</span><input v-model="providerForm.api_key" aria-label="API Key" type="password" autocomplete="new-password" /></label>
        <label class="field"><span>默认模型</span><input v-model="providerForm.default_model" aria-label="默认模型" autocomplete="off" /></label>
        <footer>
          <button v-if="snapshot.stage === 'complete'" type="button" @click="addFormOpen = false; providerForm.api_key = ''">取消</button>
          <button class="primary-command" data-action="save-provider" type="button" :disabled="busy || !canSaveProvider" @click="saveProvider">
            <Check :size="16" aria-hidden="true" /> 保存并探测
          </button>
        </footer>
      </form>
    </div>

    <div v-if="error" class="onboarding-error" role="alert">
      <code>{{ error.reason_code }}</code>
      <span>{{ error.action }}</span>
    </div>
  </section>
</template>

<style scoped>
.onboarding { padding: 22px 0; border-bottom: 1px solid var(--sage-border); }
.onboarding-step, .provider-section { display: grid; gap: 16px; }
h2 { margin: 0; font-size: 16px; font-weight: 650; letter-spacing: 0; }
button, input, select { font: inherit; }
button { color: var(--sage-text-secondary); background: var(--sage-surface); border: 1px solid var(--sage-border); border-radius: var(--sage-radius); }
button:hover:not(:disabled) { color: var(--sage-text); border-color: var(--sage-border-strong); }
button:disabled { cursor: not-allowed; opacity: .55; }
.mode-control { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.mode-control button { display: flex; min-height: 72px; align-items: center; gap: 12px; padding: 14px; text-align: left; }
.mode-control span { display: grid; gap: 3px; }
.mode-control strong { color: var(--sage-text); }
.mode-control small, .section-heading small, .provider-summary small { color: var(--sage-text-muted); }
.inline-problem, .delete-confirm, .onboarding-error { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px; background: var(--sage-surface-subtle); border: 1px solid var(--sage-border); border-radius: var(--sage-radius); }
.inline-problem button, .primary-command { display: inline-flex; min-height: 34px; align-items: center; gap: 7px; padding: 0 12px; }
.primary-command { justify-self: end; color: var(--sage-on-brand); background: var(--sage-brand); border-color: var(--sage-brand); }
.field { display: grid; min-width: 0; gap: 6px; color: var(--sage-text-secondary); font-size: var(--sage-font-sm); }
.field input, select, .inline-editor input { width: 100%; min-width: 0; height: 36px; box-sizing: border-box; padding: 0 10px; color: var(--sage-text); background: var(--sage-surface); border: 1px solid var(--sage-border); border-radius: var(--sage-radius); }
.input-with-icon { display: grid; grid-template-columns: 20px minmax(0, 1fr); align-items: center; padding-left: 10px; border: 1px solid var(--sage-border); border-radius: var(--sage-radius); }
.input-with-icon input { border: 0; }
.section-heading, .provider-summary, .provider-controls, .provider-form footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.section-heading > div { display: grid; gap: 3px; }
.section-heading > button, .provider-controls button, .inline-editor button { display: grid; width: 34px; height: 34px; padding: 0; place-items: center; }
.provider-list { display: grid; gap: 8px; }
.provider-item { display: grid; gap: 12px; padding: 14px; border: 1px solid var(--sage-border); border-radius: var(--sage-radius); }
.provider-summary { justify-content: flex-start; }
.provider-summary > div { display: grid; flex: 1; gap: 2px; }
.provider-summary code, .onboarding-error code, .inline-problem code { color: var(--sage-text-muted); font-family: var(--sage-font-mono); font-size: var(--sage-font-xs); }
.status-dot { width: 8px; height: 8px; background: var(--sage-warning); border-radius: 50%; }
.status-dot[data-status="connected"] { background: var(--sage-success); }
.status-dot[data-status="error"], .status-dot[data-status="disconnected"] { background: var(--sage-danger); }
.provider-controls { justify-content: flex-end; }
.provider-controls select { width: min(240px, 100%); margin-right: auto; }
.inline-editor { display: grid; grid-template-columns: minmax(0, 1fr) 34px 34px; gap: 8px; }
.delete-confirm { color: var(--sage-danger); }
.delete-confirm button { min-height: 30px; padding: 0 10px; }
.provider-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding-top: 4px; }
.provider-form footer { grid-column: 1 / -1; justify-content: flex-end; }
.provider-form footer > button { min-height: 34px; padding: 0 12px; }
.onboarding-error { margin-top: 14px; color: var(--sage-danger); }
@media (max-width: 640px) {
  .mode-control, .provider-form { grid-template-columns: 1fr; }
  .provider-controls { flex-wrap: wrap; }
  .provider-controls select { width: 100%; margin-right: 0; }
}
</style>
