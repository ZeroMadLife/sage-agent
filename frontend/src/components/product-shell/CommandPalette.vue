<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowUpRight,
  BookOpenText,
  Brain,
  ChevronRight,
  Gauge,
  MessageCircle,
  MessageCirclePlus,
  Palette,
  PlugZap,
  Search,
  Settings2,
  Upload,
  X,
} from 'lucide-vue-next'
import { closeCommandPalette, openCommandPalette, useCommandPalette } from './useCommandPalette'

type CommandItem = {
  id: string
  label: string
  group: string
  keywords: string
  target: string
  icon: typeof Search
}

const commands: CommandItem[] = [
  { id: 'main', label: '主对话', group: '前往', keywords: 'chat 对话 首页', target: '/assistant', icon: MessageCircle },
  { id: 'new-chat', label: '新建主对话', group: '开始', keywords: 'new chat 新建 会话', target: '/assistant?intent=new', icon: MessageCirclePlus },
  { id: 'knowledge', label: 'Knowledge', group: '前往', keywords: '知识库 图谱 wiki', target: '/knowledge', icon: BookOpenText },
  { id: 'import', label: '导入知识来源', group: '开始', keywords: 'obsidian github markdown 导入', target: '/knowledge?intent=import', icon: Upload },
  { id: 'public', label: '公开门面', group: '前往', keywords: 'public profile 作品集 hr', target: '/public', icon: ArrowUpRight },
  { id: 'appearance', label: '外观设置', group: '设置', keywords: 'theme 主题 light dark', target: '/settings/appearance', icon: Palette },
  { id: 'providers', label: '模型与 Provider', group: '设置', keywords: 'model provider 模型', target: '/settings/providers', icon: Settings2 },
  { id: 'memory', label: '记忆', group: '设置', keywords: 'memory 记忆 proposal', target: '/settings/memory', icon: Brain },
  { id: 'mcp', label: 'MCP', group: '设置', keywords: 'mcp tools 工具', target: '/settings/mcp', icon: PlugZap },
  { id: 'usage', label: '用量', group: '设置', keywords: 'usage token cost 用量', target: '/settings/usage', icon: Gauge },
]

const router = useRouter()
const { commandPaletteOpen } = useCommandPalette()
const query = ref('')
const selectedIndex = ref(0)
const searchInput = ref<HTMLInputElement | null>(null)
const dialog = ref<HTMLElement | null>(null)
let previousBodyOverflow = ''
let bodyScrollLocked = false

const filteredCommands = computed(() => {
  const normalized = query.value.trim().toLocaleLowerCase()
  if (!normalized) return commands
  return commands.filter((command) => (
    `${command.label} ${command.group} ${command.keywords}`.toLocaleLowerCase().includes(normalized)
  ))
})

watch(
  commandPaletteOpen,
  (open) => {
    if (!open) {
      if (bodyScrollLocked) document.body.style.overflow = previousBodyOverflow
      bodyScrollLocked = false
      return
    }
    previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    bodyScrollLocked = true
    query.value = ''
    selectedIndex.value = 0
    searchInput.value?.focus()
  },
  { flush: 'post' },
)

watch(query, () => {
  selectedIndex.value = 0
})

async function executeCommand(command = filteredCommands.value[selectedIndex.value]) {
  if (!command) return
  await router.push(command.target)
  closeCommandPalette(false)
}

function handleSearchKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeCommandPalette()
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    selectedIndex.value = filteredCommands.value.length
      ? (selectedIndex.value + 1) % filteredCommands.value.length
      : 0
    return
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    selectedIndex.value = filteredCommands.value.length
      ? (selectedIndex.value - 1 + filteredCommands.value.length) % filteredCommands.value.length
      : 0
    return
  }
  if (event.key === 'Enter') {
    event.preventDefault()
    void executeCommand()
  }
}

function handleDialogKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeCommandPalette()
    return
  }
  if (event.key !== 'Tab' || !dialog.value) return
  const elements = [...dialog.value.querySelectorAll<HTMLElement>(
    'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )]
  if (!elements.length) return
  const currentIndex = elements.indexOf(document.activeElement as HTMLElement)
  const nextIndex = event.shiftKey ? currentIndex - 1 : currentIndex + 1
  if ((!event.shiftKey && (currentIndex < 0 || nextIndex >= elements.length)) || (event.shiftKey && currentIndex <= 0)) {
    event.preventDefault()
    elements[event.shiftKey ? elements.length - 1 : 0].focus()
  }
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (!(event.metaKey || event.ctrlKey) || event.key.toLocaleLowerCase() !== 'k') return
  event.preventDefault()
  if (commandPaletteOpen.value) closeCommandPalette()
  else openCommandPalette()
}

onMounted(() => window.addEventListener('keydown', handleGlobalKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleGlobalKeydown)
  if (bodyScrollLocked) document.body.style.overflow = previousBodyOverflow
})
</script>

<template>
  <div
    v-if="commandPaletteOpen"
    class="command-palette-backdrop"
    @mousedown.self="closeCommandPalette()"
  >
    <section
      ref="dialog"
      class="command-palette"
      role="dialog"
      aria-modal="true"
      aria-label="命令面板"
      @keydown.capture="handleDialogKeydown"
    >
      <header class="command-search">
        <Search :size="18" aria-hidden="true" />
        <input
          ref="searchInput"
          v-model="query"
          type="search"
          aria-label="搜索命令"
          placeholder="搜索页面或命令"
          autocomplete="off"
          :aria-activedescendant="filteredCommands[selectedIndex]?.id ? `command-${filteredCommands[selectedIndex].id}` : undefined"
          @keydown="handleSearchKeydown"
        />
        <button type="button" aria-label="关闭命令面板" title="关闭" @click="closeCommandPalette()">
          <X :size="17" />
        </button>
      </header>

      <div v-if="filteredCommands.length" class="command-results" role="listbox" aria-label="可用命令">
        <button
          v-for="(command, index) in filteredCommands"
          :id="`command-${command.id}`"
          :key="command.id"
          type="button"
          role="option"
          :aria-selected="selectedIndex === index"
          :class="{ selected: selectedIndex === index }"
          @mouseenter="selectedIndex = index"
          @click="executeCommand(command)"
        >
          <span class="command-icon"><component :is="command.icon" :size="17" /></span>
          <span class="command-copy"><strong>{{ command.label }}</strong><small>{{ command.group }}</small></span>
          <ChevronRight :size="16" class="command-chevron" />
        </button>
      </div>
      <p v-else class="command-empty">未找到匹配命令</p>
    </section>
  </div>
</template>

<style scoped>
.command-palette-backdrop {
  position: fixed;
  z-index: 100;
  inset: 0;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 12vh 16px 24px;
  background: var(--sage-overlay);
  backdrop-filter: blur(4px);
}

.command-palette {
  width: min(620px, 100%);
  overflow: hidden;
  border: 1px solid var(--sage-border-strong);
  border-radius: var(--sage-radius-lg);
  background: var(--sage-surface);
  box-shadow: var(--sage-shadow-drawer);
}

.command-search {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) 32px;
  align-items: center;
  gap: 10px;
  min-height: 58px;
  padding: 0 12px 0 18px;
  color: var(--sage-text-muted);
  border-bottom: 1px solid var(--sage-border);
}

.command-search input {
  width: 100%;
  min-width: 0;
  padding: 0;
  border: 0;
  outline: 0;
  color: var(--sage-text);
  background: transparent;
  font-size: 15px;
  line-height: 1.4;
}

.command-search input::placeholder { color: var(--sage-text-muted); }
.command-search button {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: var(--sage-radius);
  color: var(--sage-text-secondary);
  background: transparent;
}
.command-search button:hover { color: var(--sage-text); background: var(--sage-surface-muted); }

.command-results {
  max-height: min(440px, 62vh);
  overflow-y: auto;
  padding: 8px;
}

.command-results button {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) 18px;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 50px;
  padding: 6px 10px;
  border: 0;
  border-radius: var(--sage-radius);
  color: var(--sage-text);
  text-align: left;
  background: transparent;
}

.command-results button.selected { background: var(--sage-surface-muted); }
.command-icon {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  color: var(--sage-brand);
  background: var(--sage-surface);
}
.command-copy { display: flex; flex-direction: column; min-width: 0; }
.command-copy strong { overflow: hidden; font-size: var(--sage-font-md); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.command-copy small { color: var(--sage-text-muted); font-size: var(--sage-font-xs); }
.command-chevron { color: var(--sage-text-muted); }
.command-empty { margin: 0; padding: 28px 18px; color: var(--sage-text-muted); text-align: center; font-size: var(--sage-font-md); }

@media (max-width: 479px) {
  .command-palette-backdrop { padding: 0; align-items: stretch; }
  .command-palette { width: 100%; min-height: 100dvh; border: 0; border-radius: 0; box-shadow: none; }
  .command-results { max-height: calc(100dvh - 59px); }
}

@media (prefers-reduced-motion: reduce) {
  .command-palette-backdrop { backdrop-filter: none; }
}
</style>
