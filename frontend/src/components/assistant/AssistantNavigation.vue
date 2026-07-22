<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  ArrowUpRight,
  BookOpenText,
  MessageCircle,
  Search,
  Settings,
  Sparkles,
} from 'lucide-vue-next'
import { openCommandPalette } from '../product-shell'

const route = useRoute()
const compact = ref(window.innerWidth < 900)

const primaryItems = [
  { id: 'conversation', label: '主对话', target: '/assistant', icon: MessageCircle },
  { id: 'knowledge', label: 'Knowledge', target: '/knowledge', icon: BookOpenText },
]

const mobileItems = [
  ...primaryItems,
  { id: 'settings', label: '设置', target: '/settings/appearance', icon: Settings },
]

function isActive(id: string) {
  if (id === 'conversation') return route.path === '/assistant' || route.path.startsWith('/coding')
  if (id === 'knowledge') return route.path === '/knowledge'
  if (id === 'settings') return route.path.startsWith('/settings')
  return false
}

function showCommandPalette(event: MouseEvent) {
  openCommandPalette(event.currentTarget as HTMLElement)
}

function updateBreakpoint() {
  compact.value = window.innerWidth < 900
}

onMounted(() => window.addEventListener('resize', updateBreakpoint))
onBeforeUnmount(() => window.removeEventListener('resize', updateBreakpoint))
</script>

<template>
  <div class="assistant-shell">
    <aside v-if="!compact" class="assistant-navigation">
      <header class="navigation-brand">
        <span class="brand-mark"><Sparkles :size="17" /></span>
        <span><strong>Sage</strong><small>Personal Companion</small></span>
      </header>

      <nav class="desktop-primary-navigation" aria-label="主要功能">
        <RouterLink
          v-for="item in primaryItems"
          :key="item.id"
          :to="item.target"
          :class="{ active: isActive(item.id) }"
          :aria-current="isActive(item.id) ? 'page' : undefined"
        >
          <component :is="item.icon" :size="17" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <button class="command-trigger" type="button" aria-label="打开命令面板" title="搜索" @click="showCommandPalette">
        <Search :size="17" />
        <span>搜索</span>
      </button>

      <div class="navigation-spacer"></div>

      <RouterLink class="public-link" to="/public">
        <ArrowUpRight :size="17" />
        <span>公开门面</span>
      </RouterLink>
      <RouterLink class="settings-link" to="/settings/appearance">
        <Settings :size="17" />
        <span>设置</span>
      </RouterLink>
    </aside>

    <button
      v-if="compact"
      class="mobile-command-trigger"
      type="button"
      aria-label="打开命令面板"
      title="搜索"
      @click="showCommandPalette"
    >
      <Search :size="18" />
    </button>

    <main class="assistant-main">
      <slot />
    </main>

    <nav v-if="compact" class="mobile-bottom-navigation" aria-label="主要功能">
      <RouterLink
        v-for="item in mobileItems"
        :key="item.id"
        :to="item.target"
        :class="{ active: isActive(item.id) }"
        :aria-current="isActive(item.id) ? 'page' : undefined"
      >
        <component :is="item.icon" :size="19" />
        <span>{{ item.label }}</span>
      </RouterLink>
    </nav>
  </div>
</template>

<style scoped>
.assistant-shell {
  min-height: 100dvh;
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  background: var(--sage-bg);
}

.assistant-navigation {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
  padding: 16px 12px 12px;
  border-right: 1px solid var(--sage-border);
  background: var(--sage-surface);
}

.navigation-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 46px;
  padding: 0 8px 14px;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: var(--sage-radius);
  color: var(--sage-brand);
  background: var(--sage-brand-bg);
}

.navigation-brand > span:last-of-type { display: flex; flex-direction: column; min-width: 0; }
.navigation-brand strong { font-size: 17px; line-height: 1.2; }
.navigation-brand small { margin-top: 2px; color: var(--sage-text-muted); font-size: var(--sage-font-xs); line-height: 1.35; }

.desktop-primary-navigation {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: 10px;
  border-top: 1px solid var(--sage-border);
}

.desktop-primary-navigation a,
.command-trigger,
.public-link,
.settings-link {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 40px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--sage-radius);
  color: var(--sage-text-secondary);
  text-decoration: none;
  background: transparent;
  font-size: var(--sage-font-md);
  line-height: 1;
}

.desktop-primary-navigation a:hover,
.command-trigger:hover,
.public-link:hover,
.settings-link:hover {
  color: var(--sage-text);
  background: var(--sage-surface-muted);
}

.desktop-primary-navigation a.active {
  position: relative;
  color: var(--sage-brand-strong);
  background: var(--sage-brand-bg);
  font-weight: 600;
}

.desktop-primary-navigation a.active::before {
  position: absolute;
  left: 0;
  width: 2px;
  height: 18px;
  border-radius: 1px;
  background: var(--sage-brand);
  content: '';
}

.command-trigger { margin-top: 8px; text-align: left; }
.navigation-spacer { flex: 1; }
.public-link { margin-bottom: 2px; }
.settings-link { padding-top: 1px; border-top: 1px solid var(--sage-border); border-radius: 0; }
.assistant-main { min-width: 0; min-height: 100dvh; }
.mobile-command-trigger,
.mobile-bottom-navigation { display: none; }

@media (max-width: 899px) {
  .assistant-shell { display: block; min-height: 100dvh; }
  .assistant-main { min-height: 100dvh; padding-bottom: calc(64px + env(safe-area-inset-bottom)); }
  .mobile-command-trigger {
    position: fixed;
    z-index: 20;
    top: 12px;
    right: 12px;
    display: grid;
    place-items: center;
    width: 38px;
    height: 38px;
    padding: 0;
    border: 1px solid var(--sage-border);
    border-radius: var(--sage-radius);
    color: var(--sage-text-secondary);
    background: color-mix(in srgb, var(--sage-surface) 94%, transparent);
    box-shadow: var(--sage-shadow-sm);
    backdrop-filter: blur(10px);
  }
  .mobile-bottom-navigation {
    position: fixed;
    z-index: 19;
    inset: auto 0 0;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    min-height: calc(58px + env(safe-area-inset-bottom));
    padding: 4px 8px env(safe-area-inset-bottom);
    border-top: 1px solid var(--sage-border);
    background: color-mix(in srgb, var(--sage-surface) 96%, transparent);
    backdrop-filter: blur(14px);
  }
  .mobile-bottom-navigation a {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
    min-width: 0;
    min-height: 50px;
    border-radius: var(--sage-radius);
    color: var(--sage-text-muted);
    text-decoration: none;
    font-size: 11px;
    line-height: 1;
  }
  .mobile-bottom-navigation a.active { color: var(--sage-brand-strong); background: var(--sage-brand-bg); }
}

@media (prefers-reduced-motion: reduce) {
  .mobile-command-trigger,
  .mobile-bottom-navigation { backdrop-filter: none; }
}
</style>
