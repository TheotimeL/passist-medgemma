<template>
  <div class="dossier-root">
    <v-tabs v-model="highlightStore.activeTab" density="compact" color="primary" grow>
      <v-tab value="fhir">
        <v-icon start size="small">mdi-database</v-icon>
        EHR
      </v-tab>
      <v-tab value="notes">
        <v-icon start size="small">mdi-file-document</v-icon>
        Clinical Notes
        <v-icon
          v-if="highlightStore.activeEvidence"
          size="10"
          color="warning"
          class="ml-1"
        >mdi-circle</v-icon>
      </v-tab>
      <v-tab value="policy">
        <v-icon start size="small">mdi-shield-outline</v-icon>
        Policy
        <v-icon
          v-if="highlightStore.activeCriterionId && highlightStore.activeTab !== 'policy'"
          size="10"
          color="primary"
          class="ml-1"
        >mdi-circle</v-icon>
      </v-tab>
    </v-tabs>

    <v-divider />

    <div class="dossier-scroll">
      <!-- EHR/FHIR Data Tab -->
      <div v-if="highlightStore.activeTab === 'fhir'" class="pa-3">
        <FhirSummary v-if="patientStore.fhirData" :data="patientStore.fhirData" />
        <v-skeleton-loader v-if="patientStore.loadingFhir && !patientStore.fhirData" type="card, card, card" />
        <div
          v-if="!patientStore.loadingFhir && !patientStore.fhirData"
          class="d-flex flex-column align-center justify-center pa-8"
        >
          <v-icon size="32" color="grey-lighten-1">mdi-database-off</v-icon>
          <span class="text-body-2 text-medium-emphasis mt-2">No EHR data available</span>
        </div>
      </div>

      <!-- Clinical Notes Tab -->
      <div v-if="highlightStore.activeTab === 'notes'" class="pa-3">
        <NoteViewer
          v-if="patientStore.notes.length > 0"
          :content="patientStore.notes[0].content"
          :highlight-text="highlightStore.activeEvidence"
        />
        <div v-else-if="patientStore.loadingNotes" class="d-flex flex-column align-center justify-center pa-8">
          <v-progress-circular indeterminate color="primary" size="28" class="mb-3" />
          <span class="text-caption text-medium-emphasis">Loading clinical notes...</span>
        </div>
        <div v-else class="d-flex flex-column align-center justify-center pa-8">
          <v-icon size="32" color="grey-lighten-1">mdi-file-document-outline</v-icon>
          <span class="text-body-2 text-medium-emphasis mt-2">No clinical notes found</span>
        </div>
      </div>

      <!-- Policy Tab -->
      <div v-if="highlightStore.activeTab === 'policy'" class="pa-3">
        <PolicyTextView :active-criterion-id="highlightStore.activeCriterionId" />
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { usePatientStore } from '@/stores/patient'
import { useHighlightStore } from '@/stores/highlight'
import NoteViewer from '@/components/NoteViewer.vue'
import FhirSummary from '@/components/FhirSummary.vue'
import PolicyTextView from '@/components/PolicyTextView.vue'

const patientStore = usePatientStore()
const highlightStore = useHighlightStore()
</script>

<style scoped>
.dossier-root {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.dossier-scroll {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
</style>
