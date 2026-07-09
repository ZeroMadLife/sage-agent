<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  CodingApprovalCard,
  CodingComposer,
  CodingFileTree,
  CodingGitBadge,
  CodingPlanApproval,
  CodingPlanPreview,
  CodingSidebar,
  CodingThinkingIndicator,
  CodingToolActivity,
} from '../components/coding'
import { useCodingStore } from '../stores/coding'
import { useMarkdown } from '../composables/useMarkdown'

const store = useCodingStore()
const messagesRef = ref<HTMLElement | null>(null)
const composerRef = ref<InstanceType<typeof CodingComposer> | null>(null)
const showPlanPreview = ref(false)
const { render } = useMarkdown()

// Show the thinking bar only while thinking and before any tool activity card
// has appeared for the in-flight turn.
const showThinkingIndicator = computed(() => {
  if (!store.isThinking || !store.thinkingPhase) return false
  const last = store.messages[store.messages.length - 1]
  if (last && last.tools && last.tools.length > 0) return false
  return true
})

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

const isAtBottom = ref(true)

function handleScroll() {
  if (!messagesRef.value) return
  const { scrollTop, scrollHeight, clientHeight } = messagesRef.value
  isAtBottom.value = scrollHeight - scrollTop - clientHeight < 80
}

function autoScroll() {
  if (isAtBottom.value) {
    scrollToBottom()
  }
}

watch(
  () => store.messages.map(m => m.content + (m.tools ? m.tools.length : 0)).join(''),
  () => autoScroll(),
)

function onUseSkill(command: string) {
  composerRef.value?.setInput(command)
}

function openPlanPreview() {
  showPlanPreview.value = true
}

onMounted(async () => {
  await store.initialize()
  scrollToBottom()
})

onBeforeUnmount(() => {
  store.disconnect()
})
</script>

<template>
  <div class="sage-view">
    <header class="sage-header">
      <h1 class="sage-brand">Sage</h1>
      <CodingGitBadge />
      <span class="session-pill">
        {{ store.sessionId ? '已连接' : '连接中' }}
      </span>
    </header>

    <div v-if="store.runtimeMode === 'plan'" class="plan-banner">
      <span class="plan-icon">📋</span>
      <span class="plan-label">计划模式</span>
      <span class="plan-topic">{{ store.planTopic }}</span>
      <button v-if="store.planPath" class="plan-view-btn" @click="openPlanPreview">
        查看计划
      </button>
    </div>

    <div class="sage-body">
      <div class="pane-left">
        <CodingSidebar @use-skill="onUseSkill" />
      </div>

      <main class="pane-center">
        <section ref="messagesRef" class="message-area" @scroll="handleScroll">
          <div v-if="store.messages.length === 0" class="empty-state">
            <p>Sage 已就绪。输入任务或 /review 调用 skill。</p>
          </div>
          <template v-for="(msg, i) in store.messages" :key="i">
            <div v-if="msg.tools && msg.tools.length > 0" class="activity-row">
              <CodingToolActivity :tools="msg.tools" :is-thinking="!!msg.isThinking" />
            </div>
            <article v-if="msg.content" :class="['message', msg.role]">
              <div v-html="render(msg.content)" class="message-content"></div>
            </article>
          </template>
          <CodingThinkingIndicator v-if="showThinkingIndicator" :phase="store.thinkingPhase" />
          <CodingPlanApproval v-if="store.planReview" />
          <p v-if="store.errorMessage" class="error-text">{{ store.errorMessage }}</p>
        </section>

        <CodingApprovalCard
          v-if="store.pendingApproval"
          :approval="store.pendingApproval"
          :busy="false"
          @respond="store.respondApproval"
        />
        <CodingComposer ref="composerRef" />
      </main>

      <div class="pane-right">
        <CodingFileTree />
      </div>
    </div>

    <CodingPlanPreview
      v-if="showPlanPreview"
      :plan-path="store.planPath"
      :topic="store.planTopic"
      :visible="true"
      @close="showPlanPreview = false"
    />
  </div>
</template>

<style scoped>
.sage-view {
  display: grid;
  grid-template-rows: auto 1fr;
  height: 100vh;
  background: #ffffff;
}

.sage-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  border-bottom: 1px solid #e5e7eb;
  background: #fff;
}

.sage-brand {
  margin: 0;
  font-size: 18px;
  font-weight: 800;
  color: #111827;
}

.session-pill {
  margin-left: auto;
  padding: 3px 10px;
  border: 1px solid #d1d5db;
  border-radius: 999px;
  color: #374151;
  background: #f9fafb;
  font-size: 11px;
}

.sage-body {
  display: grid;
  grid-template-columns: 240px 1fr 300px;
  min-height: 0;
  overflow: hidden;
}

.plan-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  background: #eff6ff;
  color: #1e40af;
  border-bottom: 1px solid #dbeafe;
  font-size: 13px;
}

.plan-icon {
  font-size: 15px;
}

.plan-label {
  font-weight: 700;
}

.plan-topic {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  opacity: 0.85;
}

.plan-view-btn {
  margin-left: auto;
  padding: 3px 10px;
  border: 1px solid #1e40af;
  border-radius: 6px;
  background: #fff;
  color: #1e40af;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.plan-view-btn:hover {
  background: #dbeafe;
}

.pane-left {
  min-height: 0;
  overflow: hidden;
}

.pane-center {
  position: relative;
  display: grid;
  grid-template-rows: 1fr auto;
  min-height: 0;
  border-right: 1px solid #e5e7eb;
}

.pane-right {
  min-height: 0;
  overflow: hidden;
}

.message-area {
  min-height: 0;
  overflow-y: auto;
  padding: 16px 20px;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #9ca3af;
  font-size: 14px;
}

.activity-row {
  max-width: 760px;
  margin: 0 0 4px;
}

.message {
  max-width: 760px;
  margin: 0 0 12px;
  padding: 10px 14px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  font-size: 14px;
  line-height: 1.6;
}

.message.user {
  margin-left: auto;
  background: #eef6ff;
}

.message-content :deep(pre) {
  background: #f3f4f6;
  padding: 8px 12px;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 13px;
}

.message-content :deep(code) {
  font-family: 'SF Mono', 'Fira Code', monospace;
}

.error-text {
  color: #b91c1c;
  font-size: 13px;
}

@media (max-width: 900px) {
  .sage-body {
    grid-template-columns: 1fr;
  }

  .pane-left,
  .pane-right {
    display: none;
  }
}
</style>
