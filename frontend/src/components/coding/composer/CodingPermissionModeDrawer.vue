<script setup lang="ts">
import { Check, ChevronDown, ShieldCheck, X } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { useCodingStore } from '../../../stores/coding'
import type { PermissionMode } from '../../../types/api'

const store = useCodingStore()
const open = ref(false)
const changing = ref(false)

const modes: Array<{ value: PermissionMode; label: string; description: string }> = [
  { value: 'default', label: '默认', description: '修改文件和执行命令前请求确认。' },
  { value: 'accept_edits', label: '接受编辑', description: '自动执行文件编辑，命令仍需确认。' },
  { value: 'auto', label: '自动', description: '自动执行常规操作，危险命令仍需确认。' },
  { value: 'plan', label: '计划', description: '只读规划，不执行写入或命令工具。' },
]

const current = computed(() => modes.find((mode) => mode.value === store.permissionMode) || modes[0])

async function selectMode(mode: PermissionMode) {
  if (mode === store.permissionMode || changing.value) {
    open.value = false
    return
  }
  changing.value = true
  const changed = await store.changePermissionMode(mode)
  changing.value = false
  if (changed) open.value = false
}
</script>

<template>
  <div class="permission-mode-control">
    <button
      class="permission-trigger"
      type="button"
      :disabled="!store.sessionId || store.isThinking"
      :aria-expanded="open"
      aria-haspopup="dialog"
      @click="open = true"
    >
      <ShieldCheck :size="14" />
      <span>{{ current.label }}</span>
      <ChevronDown :size="13" />
    </button>

    <Teleport to="body">
      <div v-if="open" class="drawer-backdrop" @click.self="open = false">
        <section class="permission-drawer" role="dialog" aria-modal="true" aria-label="权限模式">
          <header class="drawer-header">
            <div>
              <p>权限模式</p>
              <h2>选择 Sage 的执行权限</h2>
            </div>
            <button class="drawer-close" type="button" aria-label="关闭" @click="open = false">
              <X :size="16" />
            </button>
          </header>

          <div class="mode-list">
            <button
              v-for="mode in modes"
              :key="mode.value"
              class="mode-option"
              :class="{ active: store.permissionMode === mode.value }"
              type="button"
              :disabled="changing"
              @click="selectMode(mode.value)"
            >
              <span class="mode-copy">
                <strong>{{ mode.label }}</strong>
                <small>{{ mode.description }}</small>
              </span>
              <Check v-if="store.permissionMode === mode.value" :size="16" />
            </button>
          </div>
        </section>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.permission-trigger {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 8px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  background: #fff;
  color: #374151;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.permission-trigger:hover:not(:disabled) {
  background: #f8fafc;
  border-color: #9ca3af;
}

.permission-trigger:disabled {
  cursor: not-allowed;
  color: #9ca3af;
}

:global(.drawer-backdrop) {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: end;
  justify-content: center;
  background: rgba(15, 23, 42, 0.24);
}

:global(.permission-drawer) {
  width: min(640px, calc(100vw - 24px));
  margin-bottom: 12px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 18px 44px rgba(15, 23, 42, 0.22);
}

.drawer-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 16px;
  border-bottom: 1px solid #e5e7eb;
}

.drawer-header p,
.drawer-header h2 {
  margin: 0;
}

.drawer-header p {
  color: #64748b;
  font-size: 12px;
}

.drawer-header h2 {
  margin-top: 3px;
  color: #111827;
  font-size: 15px;
}

.drawer-close {
  display: inline-grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #64748b;
  cursor: pointer;
}

.drawer-close:hover {
  background: #f1f5f9;
}

.mode-list {
  display: grid;
  gap: 6px;
  padding: 12px;
}

.mode-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
  padding: 11px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fff;
  color: #1f2937;
  cursor: pointer;
  text-align: left;
}

.mode-option:hover:not(:disabled) {
  border-color: #94a3b8;
  background: #f8fafc;
}

.mode-option.active {
  border-color: #2563eb;
  background: #eff6ff;
  color: #1d4ed8;
}

.mode-copy {
  display: grid;
  gap: 3px;
}

.mode-copy small {
  color: #64748b;
  font-size: 12px;
}
</style>
