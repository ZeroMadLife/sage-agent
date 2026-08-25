<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { FolderOpen, Power, RefreshCw, Settings2, X } from 'lucide-vue-next'
import DesktopOnboarding from './DesktopOnboarding.vue'
import {
  desktopCapabilities,
  desktopExit,
  desktopHostStatus,
  desktopOnboardingAction,
  desktopOnboardingStatus,
  desktopOpenDiagnostics,
  onDesktopConnectionState,
  type DesktopCapabilities,
  type DesktopHostSnapshot,
  type DesktopOnboardingAction,
  type DesktopOnboardingSnapshot,
} from '../../desktop/hostAdapter'

const emit = defineEmits<{
  availability: [available: boolean]
}>()

const snapshot = ref<DesktopHostSnapshot>({
  state: 'starting',
  reasonCode: null,
  action: null,
  session: null,
})
const capabilities = ref<DesktopCapabilities | null>(null)
const coreCapabilities = ref<DesktopCapabilities | null>(null)
const onboarding = ref<DesktopOnboardingSnapshot | null>(null)
const onboardingBusy = ref(false)
const onboardingError = ref<{ reason_code: string, action: string } | null>(null)
const managementOpen = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | undefined
let unsubscribeConnection: (() => void) | undefined

const title = computed(() => {
  if (onboarding.value && onboarding.value.stage !== 'complete') return '完成首次设置'
  if (snapshot.value.state === 'blocked') return 'Sage 无法启动'
  if (snapshot.value.state === 'degraded') {
    return capabilities.value ? 'Sage 部分能力受限' : 'Sage 正在恢复'
  }
  if (snapshot.value.state === 'ready') return 'Sage 已启动'
  return '正在启动 Sage'
})
const applicationAvailable = computed(() => Boolean(
  snapshot.value.session
  && onboarding.value?.stage === 'complete'
  && coreCapabilities.value?.capabilities.conversation?.status === 'ready'
  && coreCapabilities.value?.capabilities.rag?.status === 'ready'
))
const panelVisible = computed(() => !applicationAvailable.value || managementOpen.value)

watch(applicationAvailable, (available) => emit('availability', available), { immediate: true })

const capabilityLabels: Record<string, string> = {
  api: '本地服务',
  storage: '本地数据',
  checkpoint: '恢复点',
  provider: 'Provider',
  knowledge: '知识库',
  sandbox: '安全工具',
  data_directory: '数据目录',
  migrations: '数据迁移',
  workspace: '学习空间',
  conversation: '对话',
  rag: 'SQLite RAG',
  side_effect_tools: '副作用工具',
  postgres: 'PostgreSQL',
  web_search: 'Web Search',
}

function applyOnboarding(next: DesktopOnboardingSnapshot): void {
  onboarding.value = next
}

function normalizeOnboardingError(error: unknown): { reason_code: string, action: string } {
  if (error && typeof error === 'object') {
    const value = error as { reason_code?: unknown, action?: unknown }
    if (typeof value.reason_code === 'string' && typeof value.action === 'string') {
      return { reason_code: value.reason_code, action: value.action }
    }
  }
  return { reason_code: 'desktop_onboarding_unavailable', action: 'retry_onboarding' }
}

async function runOnboardingAction(action: DesktopOnboardingAction): Promise<void> {
  if (onboardingBusy.value) return
  onboardingBusy.value = true
  onboardingError.value = null
  try {
    let next = await desktopOnboardingAction(action)
    applyOnboarding(next)
    if (action.kind === 'add_provider') {
      const provider = next.providers.at(-1)
      if (provider) {
        next = await desktopOnboardingAction({
          kind: 'probe_provider',
          provider_id: provider.provider_id,
        })
        applyOnboarding(next)
      }
    }
  } catch (error) {
    onboardingError.value = normalizeOnboardingError(error)
  } finally {
    onboardingBusy.value = false
  }
}

async function refresh(): Promise<void> {
  if (pollTimer) clearTimeout(pollTimer)
  let hostBlocked = false
  try {
    snapshot.value = await desktopHostStatus()
    hostBlocked = snapshot.value.state === 'blocked'
    try {
      const rebuilt = await desktopOnboardingStatus()
      onboardingError.value = null
      applyOnboarding(rebuilt)
    } catch (error) {
      onboardingError.value = normalizeOnboardingError(error)
    }
    if (snapshot.value.state === 'ready') {
      try {
        const core = await desktopCapabilities()
        coreCapabilities.value = core
        capabilities.value = core
        snapshot.value = {
          ...snapshot.value,
          state: core.status,
        }
      } catch {
        capabilities.value = null
        coreCapabilities.value = null
        onboarding.value = null
        snapshot.value = {
          state: 'degraded',
          reasonCode: 'desktop_connection_lost',
          action: 'wait_for_restart',
          session: null,
        }
      }
    }
  } catch {
    capabilities.value = null
    coreCapabilities.value = null
    onboarding.value = null
    snapshot.value = {
      state: 'degraded',
      reasonCode: 'desktop_host_unavailable',
      action: 'restart_sage',
      session: null,
    }
  }
  const delay = snapshot.value.session ? 2000 : 500
  const recoverableBlocked = hostBlocked && snapshot.value.action !== 'open_diagnostics'
  if (!hostBlocked || recoverableBlocked) pollTimer = setTimeout(refresh, delay)
}

onMounted(() => {
  unsubscribeConnection = onDesktopConnectionState((state) => {
    if (state === 'degraded' && snapshot.value.state === 'ready') {
      capabilities.value = null
      coreCapabilities.value = null
      onboarding.value = null
      snapshot.value = {
        state: 'degraded',
        reasonCode: 'desktop_connection_lost',
        action: 'wait_for_restart',
        session: null,
      }
      void refresh()
    }
  })
  void refresh()
})
onBeforeUnmount(() => {
  if (pollTimer) clearTimeout(pollTimer)
  unsubscribeConnection?.()
})
</script>

<template>
  <div class="desktop-host-layer" :data-host-state="snapshot.state">
    <button
      v-if="applicationAvailable && !managementOpen"
      type="button"
      class="desktop-host-launcher"
      title="本地运行状态与 Provider"
      aria-label="本地运行状态与 Provider"
      data-action="open-provider-management"
      @click="managementOpen = true"
    >
      <Settings2 :size="18" aria-hidden="true" />
    </button>

    <div
      v-if="panelVisible"
      class="desktop-status-backdrop"
      :data-forced="!applicationAvailable"
    >
      <main
        class="desktop-status"
        role="dialog"
        aria-modal="true"
        aria-label="本地运行状态与 Provider"
      >
        <header class="desktop-status__header">
          <div>
            <p class="desktop-status__brand">Sage</p>
            <h1>{{ title }}</h1>
          </div>
          <div class="desktop-status__actions">
            <button type="button" title="重新检查" aria-label="重新检查" @click="refresh">
              <RefreshCw :size="17" aria-hidden="true" />
            </button>
            <button
              v-if="snapshot.action === 'open_diagnostics'"
              type="button"
              title="打开诊断"
              aria-label="打开诊断"
              data-action="diagnostics"
              @click="desktopOpenDiagnostics"
            >
              <FolderOpen :size="17" aria-hidden="true" />
            </button>
            <button
              type="button"
              title="退出 Sage"
              aria-label="退出 Sage"
              data-action="exit"
              @click="desktopExit"
            >
              <Power :size="17" aria-hidden="true" />
            </button>
            <button
              v-if="applicationAvailable"
              type="button"
              title="关闭"
              aria-label="关闭本地运行状态与 Provider"
              data-action="close-provider-management"
              @click="managementOpen = false"
            >
              <X :size="17" aria-hidden="true" />
            </button>
          </div>
        </header>

        <DesktopOnboarding
          v-if="onboarding"
          :snapshot="onboarding"
          :busy="onboardingBusy"
          :error="onboardingError"
          @action="runOnboardingAction"
        />

        <section v-if="snapshot.reasonCode && snapshot.reasonCode !== onboarding?.reason_code" class="desktop-problem" aria-live="polite">
          <code>{{ snapshot.reasonCode }}</code>
          <span>{{ snapshot.action }}</span>
        </section>

        <section v-if="capabilities && onboarding?.stage === 'complete'" class="desktop-capabilities" aria-label="本地能力状态">
          <div
            v-for="(capability, name) in capabilities.capabilities"
            :key="name"
            class="desktop-capability"
            :data-capability="name"
            :data-status="capability.status"
          >
            <span class="desktop-capability__dot" aria-hidden="true" />
            <div class="desktop-capability__content">
              <strong>{{ capabilityLabels[name] ?? name }}</strong>
              <code v-if="capability.reason_code">{{ capability.reason_code }}</code>
            </div>
            <span class="desktop-capability__status">{{ capability.status }}</span>
            <code v-if="capability.action" class="desktop-capability__action">{{ capability.action }}</code>
          </div>
        </section>

        <div v-else-if="!onboarding && !snapshot.reasonCode" class="desktop-progress" role="status" aria-live="polite">
          <span class="desktop-progress__bar" />
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.desktop-host-layer { position: relative; z-index: 60; }

.desktop-host-launcher {
  position: fixed;
  z-index: 32;
  right: 16px;
  bottom: 16px;
  box-shadow: var(--sage-shadow-sm);
}

.desktop-status-backdrop {
  position: fixed;
  z-index: 60;
  inset: 0;
  overflow: auto;
  background: rgb(17 18 20 / 42%);
}

.desktop-status-backdrop[data-forced="true"] { background: var(--sage-bg); }

.desktop-status {
  width: min(760px, calc(100% - 48px));
  max-height: calc(100dvh - 48px);
  box-sizing: border-box;
  margin: 24px auto;
  padding: 28px;
  overflow: auto;
  color: var(--sage-text);
  background: var(--sage-surface);
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  box-shadow: var(--sage-shadow-md);
}

.desktop-status-backdrop[data-forced="true"] .desktop-status {
  min-height: calc(100dvh - 48px);
  background: transparent;
  border-color: transparent;
  box-shadow: none;
}

.desktop-status__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--sage-border);
}

.desktop-status__brand {
  margin: 0 0 4px;
  color: var(--sage-brand);
  font-size: var(--sage-font-sm);
  font-weight: 700;
}

h1 {
  margin: 0;
  font-size: 24px;
  font-weight: 650;
  letter-spacing: 0;
}

.desktop-status__actions {
  display: flex;
  gap: 8px;
}

button {
  display: grid;
  width: 34px;
  height: 34px;
  padding: 0;
  place-items: center;
  color: var(--sage-text-secondary);
  background: var(--sage-surface);
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
}

button:hover { color: var(--sage-text); border-color: var(--sage-border-strong); }

.desktop-capabilities { display: grid; }

.desktop-capability {
  display: grid;
  grid-template-columns: 10px minmax(130px, 1fr) auto minmax(150px, auto);
  align-items: center;
  gap: 12px;
  min-height: 58px;
  border-bottom: 1px solid var(--sage-border);
}

.desktop-capability__dot {
  width: 8px;
  height: 8px;
  background: var(--sage-success);
  border-radius: 50%;
}

[data-status="degraded"] .desktop-capability__dot { background: var(--sage-warning); }
[data-status="blocked"] .desktop-capability__dot { background: var(--sage-danger); }

.desktop-capability__content { display: flex; flex-direction: column; min-width: 0; }
.desktop-capability__content strong { font-size: var(--sage-font-md); }
.desktop-capability code,
.desktop-problem code { color: var(--sage-text-muted); font-family: var(--sage-font-mono); font-size: var(--sage-font-xs); }
.desktop-capability__status { color: var(--sage-text-secondary); font-size: var(--sage-font-sm); }
.desktop-capability__action { text-align: right; overflow-wrap: anywhere; }

.desktop-problem {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  padding: 20px 0;
  color: var(--sage-text-secondary);
  border-bottom: 1px solid var(--sage-border);
}

.desktop-progress { height: 2px; margin-top: 24px; overflow: hidden; background: var(--sage-border); }
.desktop-progress__bar { display: block; width: 40%; height: 100%; background: var(--sage-brand); animation: progress 1.2s ease-in-out infinite alternate; }

@keyframes progress { to { transform: translateX(150%); } }

@media (max-width: 640px) {
  .desktop-host-launcher { right: 12px; bottom: calc(76px + env(safe-area-inset-bottom)); }
  .desktop-status { width: min(100% - 24px, 760px); max-height: calc(100dvh - 24px); margin: 12px auto; padding: 20px; }
  .desktop-status-backdrop[data-forced="true"] .desktop-status { min-height: calc(100dvh - 24px); }
  .desktop-capability { grid-template-columns: 10px minmax(100px, 1fr) auto; }
  .desktop-capability__action { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .desktop-progress__bar { animation: none; }
}
</style>
