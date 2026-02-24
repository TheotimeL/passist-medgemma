<template>
  <div class="landing-wrapper">
    <!-- Floating background shapes -->
    <div class="bg-shape shape-1" />
    <div class="bg-shape shape-2" />
    <div class="bg-shape shape-3" />

    <v-container class="fill-height" fluid>
      <v-row align="center" justify="center">
        <v-col cols="12" sm="10" md="8" lg="6" xl="5">
          <v-fade-transition appear>
            <div>
              <!-- Hero text above card -->
              <div class="text-center mb-8">
                <div class="hero-icon mb-5">
                  <svg width="56" height="56" viewBox="0 0 64 64" fill="none">
                    <path d="M32 8 L52 20 V40 C52 52 32 58 32 58 S12 52 12 40 V20 Z" fill="none" stroke="url(#iconGrad)" stroke-width="3" stroke-linejoin="round"/>
                    <path d="M24 34 L29 39 L40 26" fill="none" stroke="url(#iconGrad)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
                    <defs>
                      <linearGradient id="iconGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                        <stop offset="0%" stop-color="#4285F4"/>
                        <stop offset="100%" stop-color="#7B61FF"/>
                      </linearGradient>
                    </defs>
                  </svg>
                </div>
                <h1 class="text-h3 font-weight-bold hero-title">
                  PAssist
                </h1>
                <p class="text-body-1 mt-3 hero-subtitle">
                  Evidence-Linked Assistant for Prior Authorization
                </p>
              </div>

              <!-- Main card -->
              <v-card class="pa-8 landing-card" elevation="0">
                <!-- Patient Selection -->
                <v-autocomplete
                  v-model="selectedPatient"
                  :items="patientItems"
                  item-title="text"
                  item-value="value"
                  label="Select Patient"
                  prepend-inner-icon="mdi-account-outline"
                  :loading="patientStore.loadingPatients"
                  clearable
                  no-data-text="No patients found"
                  class="mb-2"
                >
                  <template #item="{ item, props: itemProps }">
                    <v-list-item v-bind="itemProps">
                      <template #subtitle>
                        <span class="text-caption" style="color: #5F6368">{{ item.raw.eligibility_reason }}</span>
                      </template>
                      <template #append>
                        <v-chip
                          v-if="item.raw.eligible === true"
                          size="small"
                          color="success"
                          variant="tonal"
                          class="font-weight-medium"
                        >
                          Eligible
                        </v-chip>
                        <v-chip
                          v-else-if="item.raw.eligible === false"
                          size="small"
                          color="error"
                          variant="tonal"
                          class="font-weight-medium"
                        >
                          Not Eligible
                        </v-chip>
                      </template>
                    </v-list-item>
                  </template>
                </v-autocomplete>

                <!-- Insurer & Drug in a row -->
                <v-row dense class="mb-2">
                  <v-col cols="12" sm="6">
                    <v-select
                      v-model="selectedInsurer"
                      :items="insurerItems"
                      label="Insurer"
                      prepend-inner-icon="mdi-shield-check-outline"
                    />
                  </v-col>
                  <v-col cols="12" sm="6">
                    <v-select
                      v-model="selectedDrug"
                      :items="drugItems"
                      label="Drug"
                      prepend-inner-icon="mdi-pill"
                    />
                  </v-col>
                </v-row>

                <!-- Start Button -->
                <v-btn
                  block
                  size="x-large"
                  color="primary"
                  :disabled="!selectedPatient"
                  :loading="navigating"
                  class="start-btn mt-2"
                  @click="startReview"
                >
                  Start Authorization Review
                  <v-icon end>mdi-arrow-right</v-icon>
                </v-btn>
              </v-card>

              <!-- Footer -->
              <p class="text-center text-caption mt-6 footer-text">
                AI drafts, human verifies &mdash; all processing runs locally
              </p>
            </div>
          </v-fade-transition>
        </v-col>
      </v-row>
    </v-container>
  </div>
</template>

<script lang="ts" setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { usePatientStore } from '@/stores/patient'

interface PolicyInfo {
  slug: string
  drug: string
  disease: string
  insurer: string
}

const router = useRouter()
const patientStore = usePatientStore()

const selectedPatient = ref<string | null>(null)
const selectedInsurer = ref('UHC')
const selectedDrug = ref('')
const selectedPolicy = ref('')
const navigating = ref(false)

const patientItems = computed(() =>
  patientStore.patients.map(p => ({
    text: p.name,
    value: p.uuid,
    eligible: p.eligible,
    eligibility_reason: p.eligibility_reason,
  }))
)

const insurerItems = ref<string[]>(['UHC'])
const drugItems = ref<string[]>([])
const policies = ref<PolicyInfo[]>([])

// Map drug display name → policy slug
const drugToSlug = computed(() => {
  const map: Record<string, string> = {}
  for (const p of policies.value) {
    map[p.drug] = p.slug
  }
  return map
})

// Keep selectedPolicy in sync with selectedDrug
watch(selectedDrug, (drug) => {
  selectedPolicy.value = drugToSlug.value[drug] || ''
})

async function fetchConfig() {
  try {
    const res = await fetch('/api/config')
    const data = await res.json()
    if (data.policies?.length) policies.value = data.policies
    if (data.drugs?.length) drugItems.value = data.drugs
    if (data.insurers?.length) insurerItems.value = data.insurers
    if (!selectedDrug.value && drugItems.value.length) {
      selectedDrug.value = drugItems.value[0] ?? ''
    }
  } catch {
    drugItems.value = ['Adalimumab (Humira)']
    if (!selectedDrug.value) selectedDrug.value = drugItems.value[0] ?? ''
  }
}

// Re-fetch patients when selected policy changes (drug switch)
watch(selectedPolicy, (policy) => {
  selectedPatient.value = null
  patientStore.fetchPatients(policy || undefined)
})

onMounted(async () => {
  await fetchConfig()
  // Initial patient fetch will be triggered by the selectedPolicy watcher
  // after fetchConfig sets the default drug → policy
})

function startReview() {
  if (selectedPatient.value) {
    navigating.value = true
    const params = new URLSearchParams({ drug: selectedDrug.value })
    if (selectedPolicy.value) params.set('policy', selectedPolicy.value)
    router.push(`/workspace/${selectedPatient.value}?${params.toString()}`)
  }
}
</script>

<style scoped>
.landing-wrapper {
  position: relative;
  min-height: 100vh;
  overflow: hidden;
  background: linear-gradient(145deg, #F0F4FF 0%, #F8F9FA 40%, #F3EEFF 100%);
}

/* Floating decorative shapes */
.bg-shape {
  position: absolute;
  border-radius: 50%;
  opacity: 0.4;
  filter: blur(80px);
  pointer-events: none;
}
.shape-1 {
  width: 500px;
  height: 500px;
  background: #4285F4;
  top: -200px;
  right: -100px;
  opacity: 0.08;
}
.shape-2 {
  width: 400px;
  height: 400px;
  background: #7B61FF;
  bottom: -150px;
  left: -100px;
  opacity: 0.07;
}
.shape-3 {
  width: 300px;
  height: 300px;
  background: #34A853;
  top: 50%;
  left: 60%;
  opacity: 0.05;
}

.hero-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 88px;
  height: 88px;
  border-radius: 24px;
  background: white;
  box-shadow: 0 4px 24px rgba(66, 133, 244, 0.12);
}

.hero-title {
  color: #1A1A2E;
  letter-spacing: -0.5px;
}

.hero-subtitle {
  color: #5F6368;
  font-size: 1.05rem;
}

.landing-card {
  border: 1px solid rgba(0, 0, 0, 0.06);
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(16px);
  transition: box-shadow 0.3s ease, transform 0.3s ease;
}
.landing-card:hover {
  box-shadow: 0 8px 32px rgba(66, 133, 244, 0.1) !important;
  transform: translateY(-2px);
}

.start-btn {
  font-weight: 600;
  letter-spacing: 0.3px;
  text-transform: none;
  font-size: 1rem;
}

.footer-text {
  color: #9AA0A6;
}
</style>
