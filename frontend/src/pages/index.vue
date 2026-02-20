<template>
  <v-container class="fill-height" fluid>
    <v-row align="center" justify="center">
      <v-col cols="12" sm="8" md="6" lg="5">
        <v-fade-transition appear>
          <v-card class="pa-8 landing-card" elevation="2">
            <!-- Header -->
            <div class="text-center mb-8">
              <div class="gemini-icon mb-4">
                <v-icon size="48" color="primary">mdi-file-document-check</v-icon>
              </div>
              <h1 class="text-h4 font-weight-medium" style="color: #202124">
                Prior Authorization
              </h1>
              <p class="text-body-1 mt-2" style="color: #5F6368">
                AI-assisted form filling powered by MedGemma
              </p>
            </div>

            <!-- Patient Selection -->
            <v-select
              v-model="selectedPatient"
              :items="patientItems"
              item-title="text"
              item-value="value"
              label="Select Patient"
              prepend-inner-icon="mdi-account"
              :loading="patientStore.loadingPatients"
              class="mb-4"
            >
              <template #item="{ props: itemProps, item }">
                <v-list-item v-bind="itemProps">
                  <template #append>
                    <v-chip
                      v-if="item.raw.metCount != null"
                      size="x-small"
                      :color="item.raw.metCount === item.raw.totalCount ? 'success' : item.raw.metCount > item.raw.totalCount / 2 ? 'warning' : 'error'"
                      variant="tonal"
                    >
                      {{ item.raw.metCount }}/{{ item.raw.totalCount }} met
                    </v-chip>
                    <v-chip
                      v-else
                      size="x-small"
                      color="grey"
                      variant="tonal"
                    >
                      Pending
                    </v-chip>
                  </template>
                </v-list-item>
              </template>
            </v-select>

            <!-- Insurer Selection -->
            <v-select
              v-model="selectedInsurer"
              :items="insurerItems"
              label="Select Insurer"
              prepend-inner-icon="mdi-shield-check"
              class="mb-4"
            />

            <!-- Drug Selection -->
            <v-select
              v-model="selectedDrug"
              :items="drugItems"
              label="Drug to Prescribe"
              prepend-inner-icon="mdi-pill"
              class="mb-6"
            />

            <!-- Start Button -->
            <v-btn
              block
              size="large"
              color="primary"
              :disabled="!selectedPatient"
              :loading="navigating"
              @click="startReview"
            >
              <v-icon start>mdi-play</v-icon>
              Start Authorization Review
            </v-btn>
          </v-card>
        </v-fade-transition>

        <!-- Footer -->
        <p class="text-center text-caption mt-6" style="color: #9AA0A6">
          AI drafts, human verifies
        </p>
      </v-col>
    </v-row>
  </v-container>
</template>

<script lang="ts" setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { usePatientStore } from '@/stores/patient'

const router = useRouter()
const patientStore = usePatientStore()

const selectedPatient = ref<string | null>(null)
const selectedInsurer = ref('BCBS Texas')
const selectedDrug = ref('Adalimumab (Humira)')
const navigating = ref(false)

const patientItems = computed(() =>
  patientStore.patients.map(p => ({
    text: p.name,
    value: p.uuid,
    metCount: p.met_count,
    totalCount: p.total_count,
  }))
)

const insurerItems = ['BCBS Texas']
const drugItems = ['Adalimumab (Humira)']

onMounted(() => {
  patientStore.fetchPatients()
})

function startReview() {
  if (selectedPatient.value) {
    navigating.value = true
    router.push(`/workspace/${selectedPatient.value}`)
  }
}
</script>

<style scoped>
.landing-card {
  transition: box-shadow 0.2s ease;
}
.landing-card:hover {
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08) !important;
}
.gemini-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 80px;
  height: 80px;
  border-radius: 50%;
  background: linear-gradient(135deg, #E8F0FE 0%, #F3E8FD 100%);
}
</style>
