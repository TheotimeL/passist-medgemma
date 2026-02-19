import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useHighlightStore = defineStore('highlight', () => {
  const activeEvidence = ref<string | null>(null)
  const activeCriterionId = ref<string | null>(null)
  const activeTab = ref<'notes' | 'policy' | 'fhir'>('fhir')

  function highlightEvidence(evidence: string, criterionId?: string) {
    activeEvidence.value = evidence
    activeCriterionId.value = criterionId || null
    activeTab.value = 'notes'
  }

  function highlightPolicy(criterionId: string) {
    activeCriterionId.value = criterionId
    activeTab.value = 'policy'
  }

  function clear() {
    activeEvidence.value = null
    activeCriterionId.value = null
  }

  return {
    activeEvidence,
    activeCriterionId,
    activeTab,
    highlightEvidence,
    highlightPolicy,
    clear,
  }
})
