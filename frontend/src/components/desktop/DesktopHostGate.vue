<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { FolderOpen, Power, RefreshCw } from 'lucide-vue-next'
import {
  desktopCapabilities,
  desktopExit,
  desktopHostStatus,
  desktopOpenDiagnostics,
  onDesktopConnectionState,
  type DesktopCapabilities,
  type DesktopHostSnapshot,
} from '../../desktop/hostAdapter'

const snapshot = ref<DesktopHostSnapshot>({
  state: 'starting',
  reasonCode: null,
  action: null,
  session: null,
})
const capabilities = ref<DesktopCapabilities | null>(null)
let pollTimer: ReturnType<typeof setTimeout> | undefined
let unsubscribeConnection: (() => void) | undefined

const title = computed(() => {
  if (snapshot.value.state === 'blocked') return 'Sage 无法启动'
  if (snapshot.value.state === 'degraded') {
    return capabilities.value ? 'Sage 部分能力受限' : 'Sage 正在恢复'
  }
  if (snapshot.value.state === 'ready') return 'Sage 已启动'
  return '正在启动 Sage'
})

const capabilityLabels: Record<string, string> = {
  api: '本地服务',
  storage: '本地数据',
  checkpoint: '恢复点',
  provider: 'Provider',
  knowledge: '知识库',
  sandbox: '安全工具',
}

async function refresh(): Promise<void> {
  if (pollTimer) clearTimeout(pollTimer)
  try {
    snapshot.value = await desktopHostStatus()
    if (snapshot.value.state === 'ready') {
      try {
        capabilities.value = await desktopCapabilities()
        snapshot.value = {
          ...snapshot.value,
          state: capabilities.value.status,
          reasonCode: capabilities.value.status === 'ready' ? null : 'desktop_capability_degraded',
          action: capabilities.value.status === 'ready' ? null : 'review_capabilities',
        }
      } catch {
        capabilities.value = null
        snapshot.value = {
          state: 'degraded',
          reasonCode: 'desktop_connection_lost',
          action: 'wait_for_restart',
          session: null,
        }
      }
    }
  } catch {
    snapshot.value = {
      state: 'degraded',
      reasonCode: 'desktop_host_unavailable',
      action: 'restart_sage',
      session: null,
    }
  }
  const delay = snapshot.value.state === 'ready' ? 1000 : 500
  if (snapshot.value.state !== 'blocked') pollTimer = setTimeout(refresh, delay)
}

onMounted(() => {
  unsubscribeConnection = onDesktopConnectionState((state) => {
    if (state === 'degraded' && snapshot.value.state === 'ready') {
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
  <main class="desktop-status" :data-host-state="snapshot.state">
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
      </div>
    </header>

    <section v-if="capabilities" class="desktop-capabilities" aria-label="本地能力状态">
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

    <section v-else-if="snapshot.reasonCode" class="desktop-problem" aria-live="polite">
      <code>{{ snapshot.reasonCode }}</code>
      <span>{{ snapshot.action }}</span>
    </section>

    <div v-else class="desktop-progress" role="status" aria-live="polite">
      <span class="desktop-progress__bar" />
    </div>
  </main>
</template>

<style scoped>
.desktop-status {
  width: min(760px, calc(100% - 48px));
  margin: 0 auto;
  padding: 56px 0;
  color: var(--sage-text);
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
  .desktop-status { width: min(100% - 32px, 760px); padding: 32px 0; }
  .desktop-capability { grid-template-columns: 10px minmax(100px, 1fr) auto; }
  .desktop-capability__action { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .desktop-progress__bar { animation: none; }
}
</style>
