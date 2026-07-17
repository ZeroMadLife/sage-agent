<script setup lang="ts">
import { ArrowDown } from 'lucide-vue-next'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

export type ChatScrollAnchor = {
  eventId: string
  offset: number
}

const props = withDefaults(defineProps<{
  outputSignature: string
  messageCount: number
  sessionKey: string
  timelineReady: boolean
  scrollAnchor?: ChatScrollAnchor | null
  hasMore?: boolean
  loading?: boolean
  isEmpty?: boolean
  loadOlder?: () => Promise<void>
}>(), {
  scrollAnchor: null,
  hasMore: false,
  loading: false,
  isEmpty: false,
  loadOlder: undefined,
})

const emit = defineEmits<{
  anchorChange: [eventId: string, offset: number]
}>()

const scroller = ref<HTMLElement | null>(null)
const followOutput = ref(true)
const returnToBottomVisible = ref(false)
const unseenMessageCount = ref(0)
const observedMessageCount = ref(props.messageCount)
let scrollFrame: number | null = null
let scrollGeneration = 0
let restoreGeneration = 0

function cancelScrollFrame() {
  if (scrollFrame === null) return
  window.cancelAnimationFrame(scrollFrame)
  scrollFrame = null
}

function scrollToBottom(clearUnseen = true) {
  followOutput.value = true
  returnToBottomVisible.value = false
  cancelScrollFrame()
  nextTick(() => {
    scrollFrame = window.requestAnimationFrame(() => {
      scrollFrame = null
      if (!scroller.value) return
      scroller.value.scrollTop = scroller.value.scrollHeight
      if (clearUnseen) unseenMessageCount.value = 0
    })
  })
}

function scheduleFollowOutput() {
  if (!followOutput.value) {
    returnToBottomVisible.value = true
    return
  }
  scrollToBottom(false)
}

function findTimelineTurn(element: HTMLElement, turnId: string) {
  return [...element.querySelectorAll<HTMLElement>('[data-timeline-turn-id]')]
    .find((item) => item.dataset.timelineTurnId === turnId)
}

function restoreScrollAnchor() {
  const requestGeneration = ++restoreGeneration
  const scrollGenerationAtRequest = scrollGeneration
  nextTick(() => {
    if (requestGeneration !== restoreGeneration || scrollGeneration !== scrollGenerationAtRequest) return
    const element = scroller.value
    if (!element || !props.scrollAnchor) return scrollToBottom()
    const target = findTimelineTurn(element, props.scrollAnchor.eventId)
    if (target) element.scrollTop = target.offsetTop + props.scrollAnchor.offset
    else scrollToBottom()
  })
}

function handleScroll() {
  const element = scroller.value
  if (!element) return
  scrollGeneration += 1
  const { scrollHeight, scrollTop, clientHeight } = element
  const atBottom = scrollHeight - scrollTop - clientHeight < 80
  followOutput.value = atBottom
  returnToBottomVisible.value = !atBottom
  if (atBottom) unseenMessageCount.value = 0

  const containerTop = element.getBoundingClientRect().top
  const firstTurn = [...element.querySelectorAll<HTMLElement>('[data-timeline-turn-id]')]
    .find((item) => item.getBoundingClientRect().bottom >= containerTop)
  if (firstTurn?.dataset.timelineTurnId) {
    emit('anchorChange', firstTurn.dataset.timelineTurnId, scrollTop - firstTurn.offsetTop)
  }
}

async function handleLoadOlder() {
  if (!props.loadOlder) return
  const element = scroller.value
  const anchor = element?.querySelector<HTMLElement>('[data-timeline-turn-id]')
  const anchorId = anchor?.dataset.timelineTurnId
  const anchorOffset = anchor
    ? anchor.getBoundingClientRect().top - (element?.getBoundingClientRect().top ?? 0)
    : 0

  await props.loadOlder()
  await nextTick()

  if (element && anchorId) {
    const next = findTimelineTurn(element, anchorId)
    if (next) {
      element.scrollTop += next.getBoundingClientRect().top
        - element.getBoundingClientRect().top
        - anchorOffset
    }
  }
}

watch(() => props.outputSignature, () => {
  const nextCount = props.messageCount
  if (followOutput.value) scheduleFollowOutput()
  else {
    returnToBottomVisible.value = true
    if (nextCount > observedMessageCount.value) {
      unseenMessageCount.value += nextCount - observedMessageCount.value
    }
  }
  observedMessageCount.value = nextCount
}, { flush: 'post' })

watch(() => props.sessionKey, () => {
  unseenMessageCount.value = 0
  followOutput.value = true
  returnToBottomVisible.value = false
  observedMessageCount.value = props.messageCount
  cancelScrollFrame()
})

watch(() => [props.sessionKey, props.timelineReady], restoreScrollAnchor, { flush: 'post' })

onMounted(restoreScrollAnchor)
onBeforeUnmount(cancelScrollFrame)

defineExpose({ scrollToBottom, restoreScrollAnchor })
</script>

<template>
  <section ref="scroller" class="chat-timeline" aria-label="会话时间线" @scroll="handleScroll">
    <div class="chat-timeline-content">
      <button
        v-if="hasMore"
        type="button"
        class="load-older-btn"
        :disabled="loading"
        @click="handleLoadOlder"
      >
        {{ loading ? '正在加载...' : '加载更早记录' }}
      </button>
      <slot v-if="isEmpty" name="empty" />
      <slot />
      <button
        v-if="returnToBottomVisible"
        class="return-to-bottom"
        type="button"
        :aria-label="unseenMessageCount ? `回到底部，${unseenMessageCount} 条新消息` : '回到底部'"
        @click="scrollToBottom()"
      >
        <ArrowDown :size="15" />
        {{ unseenMessageCount ? `${unseenMessageCount} 条新消息` : '回到底部' }}
      </button>
      <span v-if="unseenMessageCount" class="sr-only" aria-live="polite">有 {{ unseenMessageCount }} 条新消息</span>
    </div>
  </section>
</template>

<style scoped>
.chat-timeline {
  min-height: 0;
  padding: 22px clamp(18px, 4vw, 56px) 24px;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.chat-timeline-content {
  width: 100%;
  min-height: 100%;
  max-width: var(--chat-content-max, 1120px);
  margin-right: auto;
  margin-left: auto;
}

.load-older-btn {
  display: block;
  min-height: 32px;
  margin: 0 auto 18px;
  padding: 0 12px;
  border: 1px solid var(--sage-border);
  border-radius: 6px;
  color: var(--sage-text-secondary);
  background: var(--sage-surface);
  font-size: var(--sage-font-xs);
}

.load-older-btn:hover { background: var(--sage-surface-muted); }

.return-to-bottom {
  position: sticky;
  bottom: 4px;
  display: flex;
  align-items: center;
  gap: 6px;
  width: max-content;
  min-height: 32px;
  margin: 16px auto 0;
  padding: 0 11px;
  border: 1px solid var(--sage-border-strong);
  border-radius: var(--sage-radius);
  color: var(--sage-text);
  background: var(--sage-surface-raised);
  box-shadow: var(--sage-shadow);
  font-size: 12px;
  font-weight: 600;
}

.return-to-bottom:hover { background: var(--sage-surface-muted); }

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  border: 0;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}

@media (max-width: 767px) {
  .chat-timeline { padding: 14px 12px 22px; }
}
</style>
