<script setup lang="ts">
import { computed, watch } from 'vue'
import AgentProgressPanel from '../components/AgentProgressPanel.vue'
import BudgetSummary from '../components/BudgetSummary.vue'
import ChatComposer from '../components/ChatComposer.vue'
import ItineraryTimeline from '../components/ItineraryTimeline.vue'
import { useChatStore } from '../stores/chat'
import { useItineraryStore } from '../stores/itinerary'

const chatStore = useChatStore()
const itineraryStore = useItineraryStore()

watch(
  () => chatStore.result,
  (result) => {
    if (result) {
      itineraryStore.setItinerary(result.itinerary)
    }
  },
)

const validationLabel = computed(() => {
  if (!chatStore.result) {
    return '未验证'
  }
  return chatStore.result.validation.passed ? '验证通过' : '需要调整'
})

function startPlanning(content: string) {
  void chatStore.startPlanning(content)
}
</script>

<template>
  <main class="workbench">
    <aside class="left-rail">
      <ChatComposer @submit="startPlanning" />
      <AgentProgressPanel :events="chatStore.events" />
      <section class="panel status-panel">
        <div class="panel-heading">
          <h2>状态</h2>
          <span>{{ chatStore.connectionStatus }}</span>
        </div>
        <p>{{ validationLabel }}</p>
        <p v-if="chatStore.errors.length" class="error-text">
          {{ chatStore.errors[chatStore.errors.length - 1].message }}
        </p>
      </section>
    </aside>
    <section class="right-workspace">
      <header class="workspace-header">
        <div>
          <h1>TourSwarm 行程工作台</h1>
          <p>AI 进度、预算和结构化行程在同一个工作区里更新。</p>
        </div>
      </header>
      <BudgetSummary :budget="itineraryStore.budget" />
      <ItineraryTimeline :itinerary="itineraryStore.itinerary" />
    </section>
  </main>
</template>
