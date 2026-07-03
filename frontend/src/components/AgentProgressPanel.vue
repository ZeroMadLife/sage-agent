<script setup lang="ts">
import type { ProgressEvent } from '../types/api'

defineProps<{ events: ProgressEvent[] }>()
</script>

<template>
  <section class="panel agent-progress">
    <div class="panel-heading">
      <h2>AI 进度</h2>
      <span>{{ events.length }} steps</span>
    </div>
    <ol v-if="events.length" class="progress-list">
      <li v-for="(event, index) in events" :key="`${event.agent}-${index}`">
        <span class="step-index">{{ index + 1 }}</span>
        <div>
          <strong>{{ event.agent }}</strong>
          <p>{{ event.message }}</p>
        </div>
      </li>
    </ol>
    <p v-else class="empty-text">等待旅游需求。</p>
  </section>
</template>
