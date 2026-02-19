<template>
  <div>
    <v-skeleton-loader v-if="loading" type="card, card, card" />

    <TransitionGroup name="policy-list">
      <v-card
        v-for="criterion in criteria"
        :key="criterion.id"
        variant="outlined"
        class="mb-3 policy-card"
        :class="{ 'policy-highlight': activeCriterionId === criterion.id }"
        :id="`policy-${criterion.id}`"
      >
        <v-card-title class="text-subtitle-2 d-flex align-center pb-1">
          <v-chip
            size="x-small"
            :color="criterion.negated ? 'secondary' : 'primary'"
            variant="tonal"
            class="mr-2"
          >
            {{ criterion.id }}
          </v-chip>
          <span class="flex-grow-1">{{ criterion.name }}</span>
          <v-chip v-if="criterion.negated" size="x-small" variant="outlined" color="secondary">
            Negated
          </v-chip>
        </v-card-title>
        <v-card-text class="pt-1">
          <p class="text-body-2 mb-2" style="color: #5F6368">
            {{ criterion.search_description }}
          </p>
          <div v-if="criterion.source_text" class="pa-2 rounded source-text-block">
            <p class="text-caption" style="color: #202124; line-height: 1.6">
              {{ criterion.source_text }}
            </p>
          </div>
          <div v-if="criterion.keywords.length > 0" class="mt-2 d-flex flex-wrap ga-1">
            <v-chip
              v-for="kw in criterion.keywords.slice(0, 6)"
              :key="kw"
              size="x-small"
              variant="tonal"
              color="primary"
            >
              {{ kw }}
            </v-chip>
            <v-chip
              v-if="criterion.keywords.length > 6"
              size="x-small"
              variant="outlined"
              color="secondary"
            >
              +{{ criterion.keywords.length - 6 }} more
            </v-chip>
          </div>
        </v-card-text>
      </v-card>
    </TransitionGroup>

    <v-alert v-if="!loading && criteria.length === 0" type="warning" variant="tonal" density="compact">
      No policy criteria available.
    </v-alert>
  </div>
</template>

<script lang="ts" setup>
import { ref, onMounted, watch, nextTick } from 'vue'

const props = defineProps<{
  activeCriterionId: string | null
}>()

interface Criterion {
  id: string
  name: string
  source_text: string
  search_description: string
  keywords: string[]
  negated: boolean
}

const criteria = ref<Criterion[]>([])
const loading = ref(true)

onMounted(async () => {
  try {
    const res = await fetch('/api/policy/criteria')
    if (res.ok) {
      criteria.value = await res.json()
    }
  } finally {
    loading.value = false
  }
})

// Auto-scroll to highlighted criterion
watch(() => props.activeCriterionId, async (newId) => {
  if (newId) {
    await nextTick()
    const el = document.getElementById(`policy-${newId}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }
})
</script>

<style scoped>
.policy-card {
  transition: all 0.2s ease;
}
.policy-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}
.policy-highlight {
  border-color: #4285F4 !important;
  border-width: 2px !important;
  background-color: #E8F0FE !important;
}
.source-text-block {
  background: #F1F3F4;
  border-left: 3px solid #4285F4;
}
.policy-list-enter-active {
  transition: all 0.3s ease;
}
.policy-list-enter-from {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
