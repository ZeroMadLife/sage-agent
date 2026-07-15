<script setup lang="ts">
import { computed, watch } from 'vue'
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

const { themeMode } = useWorkbenchPreferences()
const osTheme = useOsTheme()
const route = useRoute()
const usesAssistantShell = computed(() => route.meta.assistantShell === true)

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
          <div class="app-shell">
            <AssistantNavigation v-if="usesAssistantShell">
              <RouterView />
            </AssistantNavigation>
            <RouterView v-else />
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
