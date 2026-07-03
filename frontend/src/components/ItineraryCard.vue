<script setup lang="ts">
import type { Itinerary } from '../types/api'

defineProps<{ itinerary: Itinerary }>()
</script>

<template>
  <div class="itinerary-card" v-if="itinerary?.days?.length">
    <h4>📋 {{ itinerary.destination }} 行程</h4>
    <div v-for="day in itinerary.days" :key="day.date" class="day">
      <h5>{{ day.date }}</h5>
      <div v-for="spot in day.spots" :key="spot.spot_id" class="spot">
        {{ spot.arrival_time }}-{{ spot.departure_time }}
        {{ spot.name }} ({{ spot.ticket_price }}元)
      </div>
      <p class="cost">当日: {{ day.total_cost }}元</p>
    </div>
    <p class="total">总花费: {{ itinerary.total_cost }}元</p>
  </div>
</template>

<style scoped>
.itinerary-card {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 1rem;
  margin: 0.5rem 0;
  background: white;
}
.day { margin: 0.5rem 0; }
.spot { padding-left: 1rem; font-size: 0.875rem; color: #4b5563; }
.total { font-weight: bold; margin-top: 0.5rem; }
</style>
