<script setup lang="ts">
import { X } from 'lucide-vue-next'
import { ref, watch } from 'vue'
import { fetchCodingFile } from '../../../api/coding'
import { useCodingStore } from '../../../stores/coding'
import { useMarkdown } from '../../../composables/useMarkdown'

const props = defineProps<{
  planPath: string
  topic: string
  visible: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const store = useCodingStore()
const { render } = useMarkdown()

const content = ref('')
const loading = ref(false)
const error = ref('')

watch(
  () => [props.visible, props.planPath] as const,
  ([visible, planPath]) => {
    if (!visible || !planPath) return
    void load(planPath)
  },
  { immediate: true },
)

async function load(planPath: string) {
  if (!store.sessionId) return
  loading.value = true
  error.value = ''
  content.value = ''
  try {
    const res = await fetchCodingFile(store.sessionId, planPath)
    content.value = res.content
  } catch (e) {
    error.value = e instanceof Error ? e.message : '无法加载计划文件'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div v-if="visible" class="plan-preview-backdrop" role="presentation" @click.self="emit('close')">
    <section class="plan-preview" role="dialog" aria-modal="true" aria-label="Plan preview">
      <header class="plan-preview-header">
        <div class="plan-preview-title">
          <p class="eyebrow">计划</p>
          <h3>{{ topic || planPath }}</h3>
        </div>
        <button class="close-btn" type="button" aria-label="Close plan preview" @click="emit('close')">
          <X :size="16" />
        </button>
      </header>
      <div class="plan-preview-body">
        <p v-if="loading" class="plan-status">加载中...</p>
        <p v-else-if="error" class="plan-error">{{ error }}</p>
        <div v-else-if="content" class="plan-content" v-html="render(content)"></div>
        <p v-else class="plan-status">暂无计划内容</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.plan-preview-backdrop {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--sage-overlay);
}

.plan-preview {
  display: grid;
  grid-template-rows: auto 1fr;
  width: min(760px, 100%);
  max-height: min(720px, 88vh);
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius-lg);
  background: var(--sage-surface);
  box-shadow: var(--sage-shadow-drawer);
}

.plan-preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--sage-border);
}

.plan-preview-title .eyebrow {
  margin: 0 0 3px;
  color: var(--sage-text-muted);
  font-size: var(--sage-font-xs);
  font-weight: 700;
  text-transform: uppercase;
}

.plan-preview-title h3 {
  margin: 0;
  color: var(--sage-text);
  font-size: 14px;
}

.close-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  min-height: 30px;
  padding: 0;
  border: 1px solid var(--sage-border);
  border-radius: var(--sage-radius);
  background: var(--sage-surface);
  color: var(--sage-text-secondary);
  cursor: pointer;
}

.close-btn:hover {
  background: var(--sage-surface-muted);
}

.plan-preview-body {
  overflow: auto;
  padding: 16px 18px;
  background: var(--sage-surface);
  font-size: 14px;
  line-height: 1.6;
  color: var(--sage-text-secondary);
}

.plan-status,
.plan-error {
  margin: 0;
  color: var(--sage-text-muted);
  font-size: 13px;
}

.plan-error {
  color: var(--sage-danger);
}

.plan-content :deep(pre) {
  background: var(--sage-surface-muted);
  padding: 8px 12px;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 13px;
}

.plan-content :deep(code) {
  font-family: 'SF Mono', 'Fira Code', monospace;
}

.plan-content :deep(h1),
.plan-content :deep(h2),
.plan-content :deep(h3) {
  color: var(--sage-text);
}

.plan-content :deep(ul),
.plan-content :deep(ol) {
  padding-left: 22px;
}
</style>
