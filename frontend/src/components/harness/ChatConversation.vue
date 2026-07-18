<script setup lang="ts">
import { computed } from 'vue'
import type {
  CodingApproval,
  CodingApprovalChoice,
  CodingRunSummary,
} from '../../types/api'
import type { ChatMessageViewModel } from '../../harness/chatTypes'
import type { TimelineTurn } from '../../stores/codingTimeline'
import type { DiffInfo } from '../../stores/codingEvents'
import { useMarkdown } from '../../composables/useMarkdown'
import CodingApprovalCard from '../coding/chat/CodingApprovalCard.vue'
import CodingRunTrace from '../coding/chat/CodingRunTrace.vue'
import CodingThinkingIndicator from '../coding/chat/CodingThinkingIndicator.vue'
import ChatMessageTurn from './chat/ChatMessageTurn.vue'

const props = withDefaults(defineProps<{
  legacyMessages: ChatMessageViewModel[]
  messages: ChatMessageViewModel[]
  turns: TimelineTurn[]
  optimisticMessage?: ChatMessageViewModel | null
  activeRunId?: string
  selectedRunId?: string
  pendingApproval?: CodingApproval | null
  approvalBusy?: boolean
  showApprovalCard?: boolean
  isThinking?: boolean
  thinkingPhase?: string
  showProcess?: boolean
  runs?: CodingRunSummary[]
  diffInfoByRun?: Record<string, DiffInfo>
  errorMessage?: string
}>(), {
  optimisticMessage: null,
  activeRunId: '',
  selectedRunId: '',
  pendingApproval: null,
  approvalBusy: false,
  showApprovalCard: true,
  isThinking: false,
  thinkingPhase: '',
  showProcess: true,
  runs: () => [],
  diffInfoByRun: () => ({}),
  errorMessage: '',
})

const emit = defineEmits<{
  selectRun: [runId: string]
  respondApproval: [choice: CodingApprovalChoice]
  openRunDiff: [runId: string]
}>()

const { render } = useMarkdown()
const showThinkingIndicator = computed(() => Boolean(props.isThinking && props.thinkingPhase))
const thinkingCharacterState = computed(() => {
  if (props.pendingApproval) return 'waiting' as const
  const activeTools = props.turns.find((turn) => turn.run_id === props.activeRunId)?.tools ?? []
  if (activeTools.some((tool) => tool.status === 'running' || tool.status === 'blocked')) {
    return 'tool' as const
  }
  if (/失败|错误/.test(props.thinkingPhase)) return 'failed' as const
  return 'thinking' as const
})
const optimisticAttachedToTurn = computed(() => Boolean(
  props.optimisticMessage
  && props.activeRunId
  && props.turns.some((turn) => turn.run_id === props.activeRunId),
))

function messagesForRun(runId: string) {
  return props.messages.filter((message) => message.run_id === runId)
}

function userMessagesForRun(runId: string) {
  return messagesForRun(runId).filter((message) => message.role === 'user')
}

function assistantMessagesForRun(runId: string) {
  return messagesForRun(runId).filter((message) => message.role === 'assistant')
}

function runHasAssistantReply(runId: string) {
  return assistantMessagesForRun(runId).some((message) => message.content.trim())
}

function isActiveTurn(runId: string) {
  return props.activeRunId === runId
}

function turnHasUser(runId: string) {
  return userMessagesForRun(runId).length > 0
}

function auditForRun(runId: string) {
  return props.runs.find((run) => run.run_id === runId)?.audit
}

function shouldShowRunTrace(runId: string, toolCount: number, approvalCount: number) {
  const hasEvidence = toolCount > 0 || approvalCount > 0 || Boolean(auditForRun(runId)?.steps.length)
  return hasEvidence && (props.showProcess || isActiveTurn(runId))
}

function pendingToolForRun(runId: string) {
  return isActiveTurn(runId) ? props.pendingApproval?.tool || '' : ''
}
</script>

<template>
  <ChatMessageTurn
    v-for="(message, index) in legacyMessages"
    :key="`legacy:${index}:${message.role}:${message.content}`"
    :message="{ ...message, id: `legacy:${index}` }"
    :rendered-content="render(message.content)"
    :show-process="showProcess"
  />

  <template v-for="turn in turns" :key="turn.id">
    <div
      class="timeline-turn"
      :class="{ selected: selectedRunId === turn.run_id }"
      :data-timeline-turn-id="turn.id"
      :data-harness-selected="selectedRunId === turn.run_id"
      @click="emit('selectRun', turn.run_id)"
      @focusin="emit('selectRun', turn.run_id)"
    >
      <ChatMessageTurn
        v-if="isActiveTurn(turn.run_id) && !turnHasUser(turn.run_id) && optimisticMessage"
        :message="optimisticMessage"
        :rendered-content="render(optimisticMessage.content)"
        :show-process="showProcess"
      />
      <ChatMessageTurn
        v-for="message in userMessagesForRun(turn.run_id)"
        :key="message.id"
        :message="message"
        :rendered-content="render(message.content)"
        :show-process="showProcess"
      />
      <div
        v-if="showThinkingIndicator && isActiveTurn(turn.run_id) && !runHasAssistantReply(turn.run_id)"
        class="active-run-status"
      >
        <CodingThinkingIndicator :phase="thinkingPhase" :state="thinkingCharacterState" />
      </div>
      <CodingApprovalCard
        v-if="showApprovalCard && isActiveTurn(turn.run_id) && pendingApproval"
        :approval="pendingApproval"
        :busy="approvalBusy"
        @respond="emit('respondApproval', $event)"
      />
      <CodingRunTrace
        v-if="shouldShowRunTrace(turn.run_id, turn.tools.length, turn.approvals.length)"
        :run-id="turn.run_id"
        :tools="turn.tools"
        :audit="auditForRun(turn.run_id)"
        :active="isActiveTurn(turn.run_id)"
        :pending-tool="pendingToolForRun(turn.run_id)"
      />
      <ChatMessageTurn
        v-for="message in assistantMessagesForRun(turn.run_id)"
        :key="message.id"
        :message="message"
        :rendered-content="render(message.content)"
        :show-process="false"
        :diff-file-count="diffInfoByRun[turn.run_id]?.file_count || 0"
        @view-diff="emit('openRunDiff', turn.run_id)"
      />
    </div>
  </template>

  <ChatMessageTurn
    v-if="optimisticMessage && !optimisticAttachedToTurn"
    :message="optimisticMessage"
    :rendered-content="render(optimisticMessage.content)"
    :show-process="showProcess"
  />
  <CodingApprovalCard
    v-if="showApprovalCard && pendingApproval && !activeRunId"
    :approval="pendingApproval"
    :busy="approvalBusy"
    @respond="emit('respondApproval', $event)"
  />
  <div v-if="showThinkingIndicator && !activeRunId" class="active-run-status">
    <CodingThinkingIndicator :phase="thinkingPhase" :state="thinkingCharacterState" />
  </div>
  <p v-if="errorMessage" class="conversation-error" role="alert">{{ errorMessage }}</p>
</template>

<style scoped>
.timeline-turn { position: relative; margin-left: -6px; padding-left: 6px; border-left: 2px solid transparent; }
.timeline-turn.selected { border-left-color: color-mix(in srgb, var(--sage-success) 68%, transparent); }
.active-run-status { display: flex; width: 100%; max-width: 1120px; padding-left: 42px; }
.active-run-status :deep(.thinking-indicator) { margin: 0 0 12px; }
.conversation-error { color: var(--sage-danger); font-size: var(--sage-font-xs); }
:deep(.message-turn) { gap: 9px; margin-bottom: 18px; }
:deep(.message-avatar) { width: 26px; height: 26px; }
:deep(.message-author) { margin-bottom: 6px; }
:deep(.message-content-shell) { font-size: 14px; line-height: 1.68; }
:deep(.message-turn.user .message-content-shell) { padding: 8px 10px; }
:deep(.run-trace) { margin-bottom: 10px; }
:deep(.run-trace summary) { min-height: 38px; }
:deep(.thinking-indicator) { margin-bottom: 9px; }
:deep(.thinking-indicator .sage-character) { width: 50px; height: 50px; }
</style>
