<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  BookOpenText,
  History,
  Home,
  Menu,
  Settings,
  Share2,
  Sparkles,
  Sprout,
  X,
} from 'lucide-vue-next'

const route = useRoute()
const compact = ref(window.innerWidth < 900)
const drawerOpen = ref(false)
const menuButton = ref<HTMLButtonElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
const navigation = ref<HTMLElement | null>(null)

const items = [
  { label: '今天', target: '/assistant', icon: Home },
  { label: '历史对话', target: '/coding', icon: History },
  { label: '知识库', target: '/knowledge', icon: BookOpenText },
  { label: '成长记录', target: '/evolution', icon: Sprout },
  { label: '公开主页', target: '/public', icon: Share2 },
]

function isActive(target: string) {
  if (target === '/coding') return route.path.startsWith('/coding')
  return route.path === target
}

function openDrawer() {
  drawerOpen.value = true
  void nextTick(() => closeButton.value?.focus())
}

function closeDrawer(restoreFocus = true) {
  drawerOpen.value = false
  if (restoreFocus) void nextTick(() => menuButton.value?.focus())
}

function focusableElements() {
  if (!navigation.value) return []
  return [...navigation.value.querySelectorAll<HTMLElement>(
    'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
  )]
}

function handleNavigationKeydown(event: KeyboardEvent) {
  if (!compact.value || !drawerOpen.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    closeDrawer()
    return
  }
  if (event.key !== 'Tab') return
  const elements = focusableElements()
  if (!elements.length) return
  const index = elements.indexOf(document.activeElement as HTMLElement)
  const next = event.shiftKey ? index - 1 : index + 1
  if ((!event.shiftKey && (index < 0 || next >= elements.length)) || (event.shiftKey && index <= 0)) {
    event.preventDefault()
    elements[event.shiftKey ? elements.length - 1 : 0].focus()
  }
}

function updateBreakpoint() {
  compact.value = window.innerWidth < 900
  if (!compact.value) drawerOpen.value = false
}

onMounted(() => window.addEventListener('resize', updateBreakpoint))
onBeforeUnmount(() => window.removeEventListener('resize', updateBreakpoint))
</script>

<template>
  <div class="assistant-shell">
    <button
      v-if="compact"
      ref="menuButton"
      class="mobile-menu"
      type="button"
      aria-label="打开主菜单"
      title="打开主菜单"
      @click="openDrawer"
    >
      <Menu :size="18" />
    </button>
    <div v-if="compact && drawerOpen" class="navigation-backdrop" aria-hidden="true" @click="closeDrawer()"></div>
    <aside
      v-if="!compact || drawerOpen"
      ref="navigation"
      class="assistant-navigation"
      :class="{ drawer: compact }"
      :role="compact ? 'dialog' : undefined"
      :aria-modal="compact ? 'true' : undefined"
      :aria-label="compact ? '主菜单' : undefined"
      @keydown.capture="handleNavigationKeydown"
    >
      <header class="navigation-brand">
        <span class="brand-mark"><Sparkles :size="17" /></span>
        <span><strong>Sage</strong><small>Personal Companion</small></span>
        <button
          v-if="compact"
          ref="closeButton"
          type="button"
          aria-label="关闭主菜单"
          title="关闭主菜单"
          @click="closeDrawer()"
        ><X :size="18" /></button>
      </header>
      <nav aria-label="主要功能">
        <RouterLink
          v-for="item in items"
          :key="item.target"
          :to="item.target"
          :class="{ active: isActive(item.target) }"
          :aria-current="isActive(item.target) ? 'page' : undefined"
          @click="compact && closeDrawer(false)"
        >
          <component :is="item.icon" :size="17" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div class="navigation-spacer"></div>
      <div class="knowledge-state">
        <i></i>
        <span><strong>知识系统</strong><small>等待连接来源</small></span>
      </div>
      <RouterLink class="settings-link" to="/settings/appearance" @click="compact && closeDrawer(false)">
        <Settings :size="17" /><span>设置</span>
      </RouterLink>
    </aside>
    <main
      class="assistant-main"
      :inert="compact && drawerOpen ? true : undefined"
      :aria-hidden="compact && drawerOpen ? 'true' : undefined"
    >
      <slot />
    </main>
  </div>
</template>

<style scoped>
.assistant-shell { min-height:100dvh; display:grid; grid-template-columns:228px minmax(0,1fr); background:var(--sage-bg); }
.assistant-navigation { position:sticky; top:0; z-index:10; display:flex; flex-direction:column; min-height:100dvh; padding:14px 12px 12px; border-right:1px solid var(--sage-border); background:var(--sage-surface); }
.navigation-brand { display:flex; align-items:center; gap:10px; min-height:46px; padding:0 8px 12px; }.brand-mark { display:grid; place-items:center; width:32px; height:32px; border-radius:var(--sage-radius); color:var(--sage-brand); background:var(--sage-brand-bg); }.navigation-brand > span:last-of-type { display:flex; flex-direction:column; min-width:0; }.navigation-brand strong { font-size:17px; }.navigation-brand small { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.navigation-brand button { display:grid; place-items:center; width:32px; height:32px; margin-left:auto; padding:0; border:0; border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:transparent; }
nav { display:flex; flex-direction:column; gap:3px; padding-top:8px; border-top:1px solid var(--sage-border); }nav a,.settings-link { display:flex; align-items:center; gap:10px; min-height:40px; padding:0 10px; border-radius:var(--sage-radius); color:var(--sage-text-secondary); text-decoration:none; font-size:var(--sage-font-md); }nav a:hover,.settings-link:hover { color:var(--sage-text); background:var(--sage-surface-muted); }nav a.active { color:var(--sage-brand-strong); background:var(--sage-brand-bg); font-weight:650; }
.navigation-spacer { flex:1; }.knowledge-state { display:flex; align-items:center; gap:9px; margin:0 6px 8px; padding:10px 5px; border-top:1px solid var(--sage-border); }.knowledge-state i { width:8px; height:8px; border-radius:50%; background:var(--sage-review); }.knowledge-state span { display:flex; flex-direction:column; }.knowledge-state strong { color:var(--sage-text-secondary); font-size:var(--sage-font-sm); }.knowledge-state small { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.settings-link { border-top:1px solid var(--sage-border); border-radius:0; }
.assistant-main { min-width:0; min-height:100dvh; }.mobile-menu,.navigation-backdrop { display:none; }
@media (max-width:899px) {
  .assistant-shell { display:block; }.mobile-menu { position:fixed; z-index:20; top:12px; left:12px; display:grid; place-items:center; width:36px; height:36px; padding:0; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:var(--sage-surface); box-shadow:var(--sage-shadow-sm); }.navigation-backdrop { position:fixed; z-index:29; inset:0; display:block; background:var(--sage-overlay); }.assistant-navigation.drawer { position:fixed; z-index:30; inset:0 auto 0 0; width:min(320px,100vw); box-shadow:var(--sage-shadow-drawer); }.assistant-main { min-height:100dvh; }
}
@media (max-width:479px) { .assistant-navigation.drawer { width:100%; } }
@media (prefers-reduced-motion:reduce) { .assistant-navigation { transition:none; } }
</style>
