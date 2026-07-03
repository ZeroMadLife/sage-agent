<script setup lang="ts">
import { RefreshCw } from 'lucide-vue-next'
import type { SpotVisit } from '../types/api'

defineProps<{ spot: SpotVisit }>()
defineEmits<{ replace: [spot: SpotVisit] }>()
</script>

<template>
  <article class="spot-card">
    <div>
      <h3>{{ spot.name }}</h3>
      <p>{{ spot.arrival_time }} - {{ spot.departure_time }}</p>
      <span>门票 {{ spot.ticket_price }} 元</span>
    </div>
    <button type="button" aria-label="替换景点" @click="$emit('replace', spot)">
      <RefreshCw :size="16" aria-hidden="true" />
    </button>
  </article>
</template>
