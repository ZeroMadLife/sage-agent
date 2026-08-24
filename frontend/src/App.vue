<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  NConfigProvider,
  NMessageProvider,
  NDialogProvider,
  NLoadingBarProvider,
  darkTheme,
  zhCN,
  dateZhCN,
  useOsTheme,
} from 'naive-ui'
import { useWorkbenchPreferences } from './composables/useWorkbenchPreferences'
import { AssistantNavigation } from './components/assistant'
import { CommandPalette } from './components/product-shell'
import CloudAuthGate from './components/auth/CloudAuthGate.vue'
import { useCloudAuth } from './composables/useCloudAuth'
import DesktopHostGate from './components/desktop/DesktopHostGate.vue'
import { isDesktopRuntime } from './desktop/hostAdapter'

const { themeMode } = useWorkbenchPreferences()
const osTheme = useOsTheme()
const route = useRoute()
const usesAssistantShell = computed(() => route.meta.assistantShell === true)
const usesPrivateCommands = computed(() => route.path !== '/public')
const cloudAuthRequired = import.meta.env.VITE_CLOUD_AUTH_REQUIRED === 'true'
const cloudAuthenticated = ref(!cloudAuthRequired)
const cloudAuthChecking = ref(cloudAuthRequired)
const { check: checkCloudAuth } = useCloudAuth()
const desktopRuntime = isDesktopRuntime()

onMounted(async () => {
  if (!cloudAuthRequired) return
  window.addEventListener('sage-auth-logout', handleCloudLogout)
  cloudAuthenticated.value = await checkCloudAuth()
  cloudAuthChecking.value = false
})

function handleCloudLogout() {
  if (cloudAuthRequired) cloudAuthenticated.value = false
}

onBeforeUnmount(() => window.removeEventListener('sage-auth-logout', handleCloudLogout))

const naiveTheme = computed(() => {
  const mode = themeMode.value
  if (mode === 'dark') return darkTheme
  if (mode === 'system') return osTheme.value === 'dark' ? darkTheme : null
  return null
})

// Sync data-theme attribute on :root for our CSS token system
watch(
  () => [themeMode.value, osTheme.value] as const,
  ([mode, os]) => {
    const isDark = mode === 'dark' || (mode === 'system' && os === 'dark')
    document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light')
  },
  { immediate: true },
)
</script>

<template>
  <NConfigProvider :theme="naiveTheme" :locale="zhCN" :date-locale="dateZhCN">
    <NLoadingBarProvider>
      <NMessageProvider>
        <NDialogProvider>
          <DesktopHostGate v-if="desktopRuntime" />
          <CloudAuthGate
            v-else-if="cloudAuthRequired && !cloudAuthenticated"
            :checking="cloudAuthChecking"
            @authenticated="cloudAuthenticated = true"
          />
          <div v-else class="app-shell">
            <AssistantNavigation v-if="usesAssistantShell">
              <RouterView />
            </AssistantNavigation>
            <RouterView v-else />
            <CommandPalette v-if="usesPrivateCommands" />
          </div>
        </NDialogProvider>
      </NMessageProvider>
    </NLoadingBarProvider>
  </NConfigProvider>
</template>

<style scoped>
.app-shell {
  min-height: 100dvh;
}
</style>
