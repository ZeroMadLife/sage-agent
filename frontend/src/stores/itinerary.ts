import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { BudgetBreakdown, Itinerary } from '../types/api'

export const useItineraryStore = defineStore('itinerary', () => {
  const itinerary = ref<Itinerary | null>(null)
  const budget = ref<BudgetBreakdown | null>(null)

  function setItinerary(next: Itinerary) {
    itinerary.value = next
    budget.value = next.budget ?? null
  }

  function setBudget(next: BudgetBreakdown | null) {
    budget.value = next
  }

  return { itinerary, budget, setItinerary, setBudget }
})
