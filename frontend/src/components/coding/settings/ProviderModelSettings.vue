<script setup lang="ts">
import {
  Check,
  FlaskConical,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  Server,
  Trash2,
  X,
} from 'lucide-vue-next'
import { computed, ref } from 'vue'
import {
  createCloudModelProvider,
  deleteCloudModelProvider,
  discoverCloudModelProvider,
  setCloudModelDefault,
  testCloudModelProvider,
  updateCloudModelProvider,
} from '../../../api/coding'
import { useCodingStore } from '../../../stores/coding'
import type {
  CloudModelApiMode,
  CloudModelInput,
  CloudModelProvider,
  CodingProviderSettingsUpdate,
} from '../../../types/api'

type ProviderForm = {
  name: string
  api_mode: CloudModelApiMode
  base_url: string
  api_key: string
  models: CloudModelInput[]
}

const store = useCodingStore()
const dialogOpen = ref(false)
const editingProviderId = ref('')
const saveBusy = ref(false)
const actionBusy = ref('')
const message = ref('')
const messageIsError = ref(false)
const makeDefault = ref(true)
const localDraft = ref('')

function emptyModel(): CloudModelInput {
  return {
    model_id: '',
    display_name: '',
    context_window_tokens: null,
    output_reserve_tokens: null,
    reasoning_supported: false,
  }
}

function emptyForm(): ProviderForm {
  return {
    name: '',
    api_mode: 'openai_responses',
    base_url: 'https://api.openai.com/v1',
    api_key: '',
    models: [emptyModel()],
  }
}

const form = ref<ProviderForm>(emptyForm())
const editingProvider = computed(() => store.accountProviders.find(
  (provider) => provider.id === editingProviderId.value,
))
const canSave = computed(() => Boolean(
  form.value.name.trim()
  && form.value.base_url.trim()
  && (editingProviderId.value || form.value.api_key.trim())
  && form.value.models.length
  && form.value.models.every((model) => model.model_id.trim()),
))

function showMessage(value: string, isError = false) {
  message.value = value
  messageIsError.value = isError
}

function openCreate() {
  editingProviderId.value = ''
  form.value = emptyForm()
  makeDefault.value = store.accountDefaultModel === null
  showMessage('')
  dialogOpen.value = true
}

function openEdit(provider: CloudModelProvider) {
  editingProviderId.value = provider.id
  form.value = {
    name: provider.name,
    api_mode: provider.api_mode,
    base_url: provider.base_url,
    api_key: '',
    models: provider.models.map((model) => ({
      model_id: model.model_id,
      display_name: model.display_name,
      context_window_tokens: model.context_window_tokens,
      output_reserve_tokens: model.output_reserve_tokens,
      reasoning_supported: model.reasoning_supported,
    })),
  }
  makeDefault.value = provider.models.some(
    (model) => model.runtime_id === store.accountDefaultModel,
  )
  showMessage('')
  dialogOpen.value = true
}

function closeDialog() {
  form.value.api_key = ''
  dialogOpen.value = false
  editingProviderId.value = ''
}

function addModel() {
  form.value.models.push(emptyModel())
}

function removeModel(index: number) {
  if (form.value.models.length === 1) return
  form.value.models.splice(index, 1)
}

function normalizeTokens(value: number | null) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

function sanitizedModels(): CloudModelInput[] {
  return form.value.models.map((model) => ({
    model_id: model.model_id.trim(),
    display_name: model.display_name.trim() || model.model_id.trim(),
    context_window_tokens: normalizeTokens(model.context_window_tokens),
    output_reserve_tokens: normalizeTokens(model.output_reserve_tokens),
    reasoning_supported: model.reasoning_supported,
  }))
}

async function refreshSettings() {
  await store.loadModelProviderSettings()
  await store.bootstrapModelCatalog(true)
}

async function saveProvider() {
  if (!canSave.value || saveBusy.value) return
  saveBusy.value = true
  showMessage('')
  const apiKey = form.value.api_key.trim()
  const models = sanitizedModels()
  let saved = false
  try {
    if (editingProviderId.value) {
      await updateCloudModelProvider(editingProviderId.value, {
        name: form.value.name.trim(),
        api_mode: form.value.api_mode,
        base_url: form.value.base_url.trim(),
        models,
        ...(apiKey ? { api_key: apiKey } : {}),
      })
    } else {
      await createCloudModelProvider({
        name: form.value.name.trim(),
        api_mode: form.value.api_mode,
        base_url: form.value.base_url.trim(),
        api_key: apiKey,
        models,
        default_model_id: makeDefault.value ? models[0].model_id : null,
      })
    }
    saved = true
    await refreshSettings()
  } catch (error) {
    showMessage(error instanceof Error ? error.message : String(error), true)
  } finally {
    form.value.api_key = ''
    saveBusy.value = false
  }
  if (saved) closeDialog()
}

async function discoverModels() {
  if (!editingProviderId.value || actionBusy.value) return
  actionBusy.value = 'discover'
  showMessage('')
  try {
    const result = await discoverCloudModelProvider(editingProviderId.value)
    const existing = new Set(form.value.models.map((model) => model.model_id))
    const discovered = result.models.filter((modelId) => !existing.has(modelId))
    form.value.models.push(...discovered.map((modelId) => ({
      ...emptyModel(),
      model_id: modelId,
      display_name: modelId,
    })))
    showMessage(`已发现 ${discovered.length} 个新模型`)
  } catch (error) {
    showMessage(error instanceof Error ? error.message : String(error), true)
  } finally {
    actionBusy.value = ''
  }
}

async function testProvider(provider: CloudModelProvider) {
  if (actionBusy.value) return
  actionBusy.value = `test:${provider.id}`
  showMessage('')
  try {
    await testCloudModelProvider(provider.id)
    await store.loadModelProviderSettings()
    showMessage(`${provider.name} 连接正常`)
  } catch (error) {
    showMessage(error instanceof Error ? error.message : String(error), true)
  } finally {
    actionBusy.value = ''
  }
}

async function removeProvider(provider: CloudModelProvider) {
  if (actionBusy.value || !window.confirm(`删除 Provider “${provider.name}”？`)) return
  actionBusy.value = `delete:${provider.id}`
  showMessage('')
  try {
    await deleteCloudModelProvider(provider.id)
    await refreshSettings()
  } catch (error) {
    showMessage(error instanceof Error ? error.message : String(error), true)
  } finally {
    actionBusy.value = ''
  }
}

async function selectDefault(provider: CloudModelProvider, modelId: string) {
  if (actionBusy.value) return
  actionBusy.value = `default:${provider.id}:${modelId}`
  showMessage('')
  try {
    await setCloudModelDefault(provider.id, modelId)
    await refreshSettings()
  } catch (error) {
    showMessage(error instanceof Error ? error.message : String(error), true)
  } finally {
    actionBusy.value = ''
  }
}

async function selectSessionModel(modelId: string) {
  if (!store.sessionId || store.isThinking || modelId === store.currentModelId) return
  await store.changeModel(modelId)
}

function syncLocalDraft() {
  const settings = store.providerSettings
  if (!settings) return
  localDraft.value = JSON.stringify({
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

async function saveLocalDraft() {
  try {
    const parsed: unknown = JSON.parse(localDraft.value)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('配置根节点必须是 JSON 对象')
    }
    const saved = await store.saveProviderSettings(parsed as CodingProviderSettingsUpdate)
    showMessage(saved ? '本地 Provider 配置已保存' : store.providerSettingsError, !saved)
    if (saved) syncLocalDraft()
  } catch (error) {
    showMessage(error instanceof Error ? error.message : String(error), true)
  }
}

function readableTokens(value: number | null | undefined) {
  return value ? value.toLocaleString() : '未配置'
}

function apiModeLabel(mode: CloudModelApiMode | string) {
  return ({
    openai_chat_completions: 'OpenAI Chat Completions',
    openai_responses: 'OpenAI Responses',
    anthropic_messages: 'Anthropic Messages',
  } as Record<string, string>)[mode] ?? mode
}

syncLocalDraft()
</script>

<template>
  <section class="provider-model-settings">
    <header class="section-header">
      <div>
        <h2>模型与 Provider</h2>
        <p>{{ store.accountProviderAuthenticated ? '账号配置' : '本地工作区配置' }}</p>
      </div>
      <button
        v-if="store.accountProviderAuthenticated"
        class="primary-command"
        type="button"
        aria-label="添加 Provider"
        @click="openCreate"
      ><Plus :size="15" />添加 Provider</button>
    </header>

    <p v-if="store.accountProviderLoading" class="empty">正在加载 Provider...</p>
    <p v-else-if="store.accountProviderError" class="error" role="alert">{{ store.accountProviderError }}</p>

    <template v-if="store.accountProviderAuthenticated">
      <p v-if="store.accountProviders.length === 0" class="empty">暂无账号 Provider</p>
      <article
        v-for="provider in store.accountProviders"
        :key="provider.id"
        class="provider-card"
      >
        <header>
          <span class="provider-icon"><Server :size="16" /></span>
          <div class="provider-identity">
            <strong>{{ provider.name }}</strong>
            <span>{{ apiModeLabel(provider.api_mode) }} · {{ provider.base_url }}</span>
          </div>
          <span :class="['connection-state', provider.status]">
            {{ provider.status === 'connected' ? '已连接' : provider.status === 'error' ? '连接失败' : '未测试' }}
          </span>
          <div class="provider-actions">
            <button type="button" :aria-label="`测试 ${provider.name}`" :title="`测试 ${provider.name}`" :disabled="!!actionBusy" @click="testProvider(provider)"><FlaskConical :size="15" /></button>
            <button type="button" :aria-label="`编辑 ${provider.name}`" :title="`编辑 ${provider.name}`" @click="openEdit(provider)"><Pencil :size="15" /></button>
            <button type="button" :aria-label="`删除 ${provider.name}`" :title="`删除 ${provider.name}`" :disabled="!!actionBusy" @click="removeProvider(provider)"><Trash2 :size="15" /></button>
          </div>
        </header>
        <div class="key-status"><KeyRound :size="13" /><span>API Key {{ provider.key_hint }}</span></div>
        <div class="provider-model-list">
          <label v-for="model in provider.models" :key="model.id" class="provider-model-row">
            <input
              type="radio"
              name="account-default-model"
              :checked="store.accountDefaultModel === model.runtime_id"
              :disabled="!!actionBusy"
              @change="selectDefault(provider, model.model_id)"
            />
            <span>
              <strong>{{ model.display_name }}</strong>
              <small>{{ model.model_id }} · {{ readableTokens(model.context_window_tokens) }} tokens<span v-if="model.reasoning_supported"> · reasoning</span></small>
            </span>
            <span v-if="store.accountDefaultModel === model.runtime_id" class="default-label"><Check :size="12" />默认模型</span>
          </label>
        </div>
      </article>
    </template>

    <template v-else-if="!store.accountProviderLoading">
      <p v-if="!store.providerSettings" class="empty">Provider 配置暂不可用</p>
      <article
        v-for="provider in store.providerSettings?.providers || []"
        :key="provider.id"
        class="local-provider-row"
      >
        <span class="provider-icon"><Server :size="16" /></span>
        <div>
          <strong>{{ provider.label }}</strong>
          <p>{{ apiModeLabel(provider.api_mode) }} · {{ provider.base_url }}</p>
          <small>{{ provider.api_key_env }} · {{ provider.api_key_configured ? '环境变量已配置' : '环境变量未配置' }} · {{ provider.models.length }} 个模型</small>
        </div>
        <span class="source-label">{{ store.providerSettings?.source === 'deployment_json' ? '部署托管' : '项目配置' }}</span>
      </article>
      <details v-if="store.providerSettings?.editable" class="local-editor" @toggle="syncLocalDraft">
        <summary>编辑 .sage/settings.json</summary>
        <textarea v-model="localDraft" aria-label="Provider JSON 配置" spellcheck="false" autocomplete="off"></textarea>
        <button class="secondary-command" type="button" @click="saveLocalDraft">保存本地配置</button>
      </details>
      <p v-if="store.providerSettingsError" class="error" role="alert">{{ store.providerSettingsError }}</p>
    </template>

    <section class="model-catalog" aria-label="可用模型">
      <header><h3>可用模型</h3><span>{{ store.models.length }}</span></header>
      <p v-if="store.models.length === 0" class="empty">暂无可用模型</p>
      <label
        v-for="model in store.models"
        :key="model.id"
        class="catalog-model-row"
        :class="{ selected: model.id === store.currentModelId }"
      >
        <input
          type="radio"
          name="session-model"
          :checked="model.id === store.currentModelId"
          :disabled="!store.sessionId || store.isThinking"
          @change="selectSessionModel(model.id)"
        />
        <span>
          <strong>{{ model.label }}</strong>
          <small>{{ model.provider }} · 上下文 {{ readableTokens(model.context_window_tokens) }} · 推理 {{ model.reasoning_modes.length ? model.reasoning_modes.join(' / ') : '关闭' }}</small>
        </span>
      </label>
    </section>

    <p v-if="message" :class="messageIsError ? 'error' : 'success'" role="status">{{ message }}</p>

    <div v-if="dialogOpen" class="dialog-backdrop" role="presentation" @mousedown.self="closeDialog">
      <section class="provider-dialog" role="dialog" aria-modal="true" :aria-label="editingProviderId ? '编辑 Provider' : '添加 Provider'">
        <header>
          <div><strong>{{ editingProviderId ? '编辑 Provider' : '添加 Provider' }}</strong><span v-if="editingProvider">{{ editingProvider.name }}</span></div>
          <button type="button" aria-label="关闭 Provider 编辑" title="关闭" @click="closeDialog"><X :size="17" /></button>
        </header>
        <div class="form-grid">
          <label><span>名称</span><input v-model="form.name" aria-label="Provider 名称" autocomplete="off" /></label>
          <label><span>API 格式</span><select v-model="form.api_mode" aria-label="API 格式"><option value="openai_responses">OpenAI Responses</option><option value="openai_chat_completions">OpenAI Chat Completions</option><option value="anthropic_messages">Anthropic Messages</option></select></label>
          <label class="wide"><span>Base URL</span><input v-model="form.base_url" aria-label="Base URL" inputmode="url" autocomplete="url" /></label>
          <label class="wide"><span>API Key</span><input v-model="form.api_key" aria-label="API Key" type="password" autocomplete="new-password" :placeholder="editingProvider?.key_hint || '仅本次提交'" /></label>
        </div>
        <div class="model-editor-heading">
          <strong>模型</strong>
          <div>
            <button v-if="editingProviderId" type="button" aria-label="发现模型" title="发现模型" :disabled="!!actionBusy" @click="discoverModels"><RefreshCw :size="14" /></button>
            <button type="button" aria-label="添加模型" title="添加模型" @click="addModel"><Plus :size="14" /></button>
          </div>
        </div>
        <div class="model-editor-list">
          <div v-for="(model, index) in form.models" :key="index" class="model-form-row">
            <input v-model="model.model_id" aria-label="模型 ID" placeholder="model-id" />
            <input v-model="model.display_name" aria-label="模型显示名称" placeholder="显示名称" />
            <input v-model.number="model.context_window_tokens" aria-label="上下文 tokens" type="number" min="1024" placeholder="上下文" />
            <input v-model.number="model.output_reserve_tokens" aria-label="输出预留 tokens" type="number" min="1" placeholder="输出预留" />
            <label class="reasoning-check"><input v-model="model.reasoning_supported" type="checkbox" />推理</label>
            <button type="button" aria-label="删除模型" title="删除模型" :disabled="form.models.length === 1" @click="removeModel(index)"><Trash2 :size="14" /></button>
          </div>
        </div>
        <label v-if="!editingProviderId" class="default-check"><input v-model="makeDefault" type="checkbox" />设为账号默认模型</label>
        <p v-if="message" :class="messageIsError ? 'error' : 'success'" role="status">{{ message }}</p>
        <footer><button class="secondary-command" type="button" @click="closeDialog">取消</button><button class="primary-command save-provider" type="button" :disabled="!canSave || saveBusy" @click="saveProvider">{{ saveBusy ? '保存中...' : '保存 Provider' }}</button></footer>
      </section>
    </div>
  </section>
</template>

<style scoped>
.provider-model-settings { display:grid; gap:16px; }
.section-header { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.section-header h2 { margin:0; color:var(--sage-text); font-size:15px; }
.section-header p { margin:4px 0 0; color:var(--sage-text-muted); font-size:11px; }
.primary-command,.secondary-command { display:inline-flex; align-items:center; justify-content:center; gap:6px; min-height:32px; border:1px solid var(--sage-border-strong); border-radius:6px; padding:0 11px; font-size:11px; }
.primary-command { color:var(--sage-surface); background:var(--sage-text); }
.secondary-command { color:var(--sage-text-secondary); background:var(--sage-surface); }
.primary-command:disabled,.secondary-command:disabled { opacity:.45; }
.provider-card { border:1px solid var(--sage-border); border-radius:6px; background:var(--sage-surface); overflow:hidden; }
.provider-card > header { display:grid; grid-template-columns:32px minmax(0,1fr) auto auto; align-items:center; gap:10px; min-height:58px; padding:10px 12px; border-bottom:1px solid var(--sage-border); }
.provider-icon { display:grid; place-items:center; width:30px; height:30px; border:1px solid var(--sage-border); border-radius:6px; color:var(--sage-text-secondary); background:var(--sage-surface-muted); }
.provider-identity { display:grid; gap:3px; min-width:0; }
.provider-identity strong { color:var(--sage-text); font-size:12px; }
.provider-identity span { overflow:hidden; color:var(--sage-text-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.connection-state,.source-label,.default-label { display:inline-flex; align-items:center; gap:3px; flex:none; border:1px solid var(--sage-border); border-radius:4px; padding:2px 6px; color:var(--sage-text-muted); font-size:9px; }
.connection-state.connected { border-color:color-mix(in srgb,var(--sage-success) 35%,var(--sage-border)); color:var(--sage-success); }
.connection-state.error { border-color:color-mix(in srgb,var(--sage-danger) 35%,var(--sage-border)); color:var(--sage-danger); }
.provider-actions { display:flex; gap:3px; }
.provider-actions button,.provider-dialog header button,.model-editor-heading button,.model-form-row > button { display:grid; place-items:center; width:30px; height:30px; border:0; border-radius:5px; color:var(--sage-text-muted); background:transparent; }
.provider-actions button:hover,.provider-dialog header button:hover,.model-editor-heading button:hover,.model-form-row > button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }
.key-status { display:flex; align-items:center; gap:6px; min-height:34px; padding:0 12px; border-bottom:1px solid var(--sage-border); color:var(--sage-text-muted); font-family:var(--sage-font-mono); font-size:9px; }
.provider-model-list { display:grid; }
.provider-model-row,.catalog-model-row { display:flex; align-items:center; gap:10px; min-height:48px; padding:8px 12px; border-bottom:1px solid var(--sage-border); }
.provider-model-row:last-child,.catalog-model-row:last-child { border-bottom:0; }
.provider-model-row > span:nth-of-type(1),.catalog-model-row > span { display:grid; gap:3px; flex:1; min-width:0; }
.provider-model-row strong,.catalog-model-row strong { color:var(--sage-text); font-size:11px; }
.provider-model-row small,.catalog-model-row small { overflow:hidden; color:var(--sage-text-muted); font-size:9px; text-overflow:ellipsis; white-space:nowrap; }
.provider-model-row input,.catalog-model-row input,.default-check input,.reasoning-check input { accent-color:var(--sage-text); }
.default-label { color:var(--sage-success); }
.model-catalog { margin-top:4px; border-top:1px solid var(--sage-border); }
.model-catalog > header { display:flex; align-items:center; justify-content:space-between; min-height:44px; }
.model-catalog h3 { margin:0; color:var(--sage-text); font-size:12px; }
.model-catalog > header span { color:var(--sage-text-muted); font-size:10px; }
.catalog-model-row { padding-inline:6px; }
.catalog-model-row.selected { background:var(--sage-surface-muted); }
.local-provider-row { display:flex; align-items:center; gap:10px; min-height:62px; border-bottom:1px solid var(--sage-border); }
.local-provider-row > div { display:grid; gap:3px; flex:1; min-width:0; }
.local-provider-row strong { font-size:12px; }
.local-provider-row p,.local-provider-row small { overflow:hidden; margin:0; color:var(--sage-text-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.local-editor { border-top:1px solid var(--sage-border); padding-top:12px; }
.local-editor summary { cursor:pointer; color:var(--sage-text-secondary); font-size:11px; font-weight:700; }
.local-editor textarea { width:100%; min-height:280px; margin:10px 0; resize:vertical; border:1px solid var(--sage-border); border-radius:6px; padding:10px; color:var(--sage-code-text); background:var(--sage-code-bg); font-family:var(--sage-font-mono); font-size:10px; line-height:1.55; }
.dialog-backdrop { position:fixed; z-index:40; inset:0; display:grid; place-items:center; padding:20px; background:rgba(20,27,36,.38); }
.provider-dialog { display:grid; gap:14px; width:min(820px,100%); max-height:min(760px,calc(100dvh - 40px)); overflow:auto; border:1px solid var(--sage-border-strong); border-radius:8px; padding:16px; color:var(--sage-text); background:var(--sage-surface); box-shadow:0 18px 52px rgba(15,23,42,.2); }
.provider-dialog > header { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:34px; border-bottom:1px solid var(--sage-border); padding-bottom:10px; }
.provider-dialog > header > div { display:flex; align-items:baseline; gap:8px; }
.provider-dialog > header strong { font-size:14px; }
.provider-dialog > header span { color:var(--sage-text-muted); font-size:10px; }
.form-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.form-grid label { display:grid; gap:5px; color:var(--sage-text-muted); font-size:9px; }
.form-grid label.wide { grid-column:1 / -1; }
.form-grid input,.form-grid select,.model-form-row > input { min-width:0; height:34px; border:1px solid var(--sage-border); border-radius:5px; padding:0 8px; color:var(--sage-text); background:var(--sage-surface); font-size:11px; }
.form-grid input:focus,.form-grid select:focus,.model-form-row > input:focus { border-color:var(--sage-focus); outline:2px solid color-mix(in srgb,var(--sage-focus) 12%,transparent); }
.model-editor-heading { display:flex; align-items:center; justify-content:space-between; min-height:30px; }
.model-editor-heading strong { font-size:11px; }
.model-editor-heading > div { display:flex; gap:2px; }
.model-editor-list { display:grid; border-top:1px solid var(--sage-border); }
.model-form-row { display:grid; grid-template-columns:minmax(110px,1.2fr) minmax(110px,1fr) 90px 90px 58px 30px; align-items:center; gap:6px; min-width:0; padding:8px 0; border-bottom:1px solid var(--sage-border); }
.reasoning-check,.default-check { display:flex; align-items:center; gap:5px; color:var(--sage-text-secondary); font-size:9px; }
.provider-dialog > footer { display:flex; justify-content:flex-end; gap:7px; border-top:1px solid var(--sage-border); padding-top:12px; }
.empty { margin:0; color:var(--sage-text-muted); font-size:11px; }
.error,.success { margin:0; font-size:11px; }
.error { color:var(--sage-danger); }
.success { color:var(--sage-success); }
@media (max-width:720px) {
  .provider-card > header { grid-template-columns:32px minmax(0,1fr) auto; }
  .connection-state { grid-column:2; width:fit-content; }
  .provider-actions { grid-column:3; grid-row:1 / span 2; }
  .model-form-row { grid-template-columns:1fr 1fr 30px; }
  .model-form-row > input:nth-of-type(3),.model-form-row > input:nth-of-type(4) { grid-row:2; }
  .reasoning-check { grid-row:2; }
  .form-grid { grid-template-columns:1fr; }
  .form-grid label.wide { grid-column:auto; }
  .provider-dialog { max-height:100dvh; border-radius:0; }
  .dialog-backdrop { padding:0; }
}
</style>
