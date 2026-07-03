<script setup lang="ts">
import type { Itinerary } from '../types/api'
import EditableSpotCard from './EditableSpotCard.vue'

defineProps<{ itinerary: Itinerary | null }>()
</script>

<template>
  <section class="panel itinerary-timeline">
    <div class="panel-heading">
      <h2>{{ itinerary?.destination ?? '行程' }}</h2>
      <span>{{ itinerary?.total_cost ?? 0 }} 元</span>
    </div>
    <p v-if="!itinerary || itinerary.days.length === 0" class="empty-text">
      暂无行程，输入需求后开始规划。
    </p>
    <article v-for="day in itinerary?.days ?? []" :key="day.date" class="timeline-day">
      <div class="day-heading">
        <h3>{{ day.date }}</h3>
        <span>{{ day.total_cost }} 元</span>
      </div>
      <EditableSpotCard v-for="spot in day.spots" :key="spot.spot_id" :spot="spot" />
    </article>
  </section>
</template>
