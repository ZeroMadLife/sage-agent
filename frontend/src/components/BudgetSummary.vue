<script setup lang="ts">
import { computed } from 'vue'
import type { BudgetBreakdown } from '../types/api'

const props = defineProps<{ budget: BudgetBreakdown | null }>()

const spentRatio = computed(() => {
  if (!props.budget || props.budget.total <= 0) {
    return 0
  }
  return Math.min(100, Math.round((props.budget.spent / props.budget.total) * 100))
})
</script>

<template>
  <section class="panel budget-summary">
    <div class="panel-heading">
      <h2>预算</h2>
      <span>{{ budget?.over_budget ? '已超预算' : '预算内' }}</span>
    </div>
    <div class="budget-meter" aria-label="预算使用比例">
      <span :style="{ width: `${spentRatio}%` }" />
    </div>
    <p class="budget-number">{{ budget?.spent ?? 0 }} / {{ budget?.total ?? 0 }} 元</p>
  </section>
</template>
