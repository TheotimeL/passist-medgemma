<template>
  <div class="form-root">
    <!-- Pinned header: tabs -->
    <div class="form-header">
      <div class="tab-bar">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          class="tab-item"
          :class="{ 'tab-active': formStore.activeTab === tab.id }"
          @click="formStore.activeTab = tab.id"
        >
          <span class="tab-label">{{ tab.label }}</span>
          <span v-if="sectionPendingCount(tab.id) > 0" class="tab-badge">
            {{ sectionPendingCount(tab.id) }}
          </span>
          <span v-else-if="sectionComplete(tab.id)" class="tab-check">
            &#10003;
          </span>
        </button>
      </div>
    </div>

    <!-- Form Fields (scrollable) -->
    <div class="form-scroll">
      <!-- Section header with Accept All for section -->
      <div
        v-if="currentSectionHasFields && sectionPendingCount(formStore.activeTab) > 0"
        class="section-header"
      >
        <span class="text-caption text-medium-emphasis">
          {{ sectionPendingCount(formStore.activeTab) }} fields to review
        </span>
        <v-btn
          variant="text"
          color="success"
          size="x-small"
          @click="acceptSection(formStore.activeTab)"
        >
          <v-icon start size="14">mdi-check-all</v-icon>
          Accept Section
        </v-btn>
      </div>

      <!-- Demographics Section -->
      <template v-if="formStore.activeTab === 'demographics'">
        <div class="section-content">
          <FormField
            v-for="field in sectionFields('demographics')"
            :key="field.fieldId"
            :field="field"
            @accept="formStore.acceptField(field.fieldId)"
            @reject="formStore.rejectField(field.fieldId)"
            @edit="(val: string) => formStore.editField(field.fieldId, val)"
            @view-source="onViewSource(field)"
          />
        </div>
      </template>

      <!-- Provider Section -->
      <template v-if="formStore.activeTab === 'provider'">
        <div class="section-content">
          <FormField
            v-for="field in sectionFields('provider')"
            :key="field.fieldId"
            :field="field"
            @accept="formStore.acceptField(field.fieldId)"
            @reject="formStore.rejectField(field.fieldId)"
            @edit="(val: string) => formStore.editField(field.fieldId, val)"
            @view-source="onViewSource(field)"
          />
          <v-skeleton-loader
            v-if="sectionFields('provider').length === 0 && !extractionStore.complete"
            type="card"
            class="mb-2"
          />
        </div>
      </template>

      <!-- Diagnosis Section -->
      <template v-if="formStore.activeTab === 'diagnosis'">
        <div class="section-content">
          <FormField
            v-for="field in sectionFields('diagnosis')"
            :key="field.fieldId"
            :field="field"
            @accept="formStore.acceptField(field.fieldId)"
            @reject="formStore.rejectField(field.fieldId)"
            @edit="(val: string) => formStore.editField(field.fieldId, val)"
            @view-source="onViewSource(field)"
          />
        </div>
      </template>

      <!-- Step Therapy / Drug History Section -->
      <template v-if="formStore.activeTab === 'step_therapy'">
        <div class="section-content">
          <FormField
            v-for="field in sectionFields('step_therapy')"
            :key="field.fieldId"
            :field="field"
            @accept="formStore.acceptField(field.fieldId)"
            @reject="formStore.rejectField(field.fieldId)"
            @edit="(val: string) => formStore.editField(field.fieldId, val)"
            @view-source="onViewSource(field)"
          />
          <v-skeleton-loader
            v-if="sectionFields('step_therapy').length === 0 && extractionStore.isExtracting"
            type="card, card"
            class="mb-2"
          />
          <div
            v-if="sectionFields('step_therapy').length === 0 && extractionStore.complete"
            class="empty-state"
          >
            <v-icon size="32" color="grey-lighten-1">mdi-pill-off</v-icon>
            <span class="text-body-2 text-medium-emphasis mt-2">No prior drug history found</span>
          </div>
        </div>
      </template>

      <!-- Drug Request Section -->
      <template v-if="formStore.activeTab === 'drug_request'">
        <div class="section-content">
          <FormField
            v-for="field in sectionFields('drug_request')"
            :key="field.fieldId"
            :field="field"
            @accept="formStore.acceptField(field.fieldId)"
            @reject="formStore.rejectField(field.fieldId)"
            @edit="(val: string) => formStore.editField(field.fieldId, val)"
            @view-source="onViewSource(field)"
          />
          <v-skeleton-loader
            v-if="sectionFields('drug_request').length === 0 && extractionStore.isExtracting"
            type="card"
            class="mb-2"
          />
          <div
            v-if="sectionFields('drug_request').length === 0 && extractionStore.complete"
            class="empty-state"
          >
            <v-icon size="32" color="grey-lighten-1">mdi-prescription</v-icon>
            <span class="text-body-2 text-medium-emphasis mt-2">No drug request data extracted</span>
          </div>
        </div>
      </template>

      <!-- Clinical Justification / Criteria Section -->
      <template v-if="formStore.activeTab === 'justification'">
        <div class="section-content">
          <PolicyTreeView v-if="extractionStore.complete || extractionStore.results.length > 0" />
          <v-skeleton-loader
            v-if="extractionStore.results.length === 0 && extractionStore.isExtracting"
            type="list-item, list-item, list-item"
          />
          <div
            v-if="!extractionStore.isExtracting && extractionStore.results.length === 0 && !extractionStore.complete"
            class="empty-state"
          >
            <v-icon size="32" color="grey-lighten-1">mdi-scale-balance</v-icon>
            <span class="text-body-2 text-medium-emphasis mt-2">Run extraction to evaluate criteria</span>
          </div>
        </div>
      </template>

      <!-- PDF Preview Section -->
      <template v-if="formStore.activeTab === 'pdf_preview'">
        <div v-if="pdfUrl" class="pdf-embed-container">
          <iframe :src="pdfUrl" class="pdf-iframe" />
        </div>
        <div v-else class="empty-state" style="min-height: 300px">
          <v-icon size="48" color="grey-lighten-1">mdi-file-pdf-box</v-icon>
          <p class="text-body-2 text-medium-emphasis mt-3 mb-4">Generate a PDF preview of the filled form</p>
          <v-btn color="primary" variant="tonal" @click="emit('generatePreview')">
            <v-icon start>mdi-refresh</v-icon>
            Generate Preview
          </v-btn>
        </div>
        <div v-if="pdfUrl" class="pa-2 d-flex justify-center">
          <v-btn variant="tonal" color="primary" size="small" @click="emit('generatePreview')">
            <v-icon start size="small">mdi-refresh</v-icon>
            Regenerate
          </v-btn>
        </div>
      </template>
    </div>

    <!-- Bottom Review Bar -->
    <div class="review-bar">
      <div class="review-progress">
        <span class="review-text">
          {{ reviewedCount }}/{{ formStore.totalFilledCount }} reviewed
        </span>
        <div class="progress-track">
          <div class="progress-fill" :style="{ width: reviewProgress + '%' }" />
        </div>
      </div>
      <div class="review-chips">
        <span v-if="formStore.acceptedCount > 0" class="chip-accepted">
          {{ formStore.acceptedCount }} accepted
        </span>
        <span v-if="formStore.suggestedCount > 0" class="chip-pending">
          {{ formStore.suggestedCount }} to review
        </span>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { useFormStore, type FormField as FormFieldType } from '@/stores/form'
import { useExtractionStore } from '@/stores/extraction'
import { useHighlightStore } from '@/stores/highlight'
import FormField from '@/components/FormField.vue'
import PolicyTreeView from '@/components/PolicyTreeView.vue'

defineProps<{
  pdfUrl: string | null
}>()

const emit = defineEmits<{
  generatePreview: []
}>()

const formStore = useFormStore()
const extractionStore = useExtractionStore()
const highlightStore = useHighlightStore()

const tabs = [
  { id: 'demographics', label: 'Patient' },
  { id: 'provider', label: 'Provider' },
  { id: 'diagnosis', label: 'Diagnosis' },
  { id: 'step_therapy', label: 'Drugs' },
  { id: 'drug_request', label: 'Request' },
  { id: 'justification', label: 'Criteria' },
  { id: 'pdf_preview', label: 'PDF' },
]

const reviewedCount = computed(() =>
  Object.values(formStore.fields).filter(f => f.status !== 'suggested').length
)

const reviewProgress = computed(() => {
  const total = formStore.totalFilledCount
  if (total === 0) return 0
  return (reviewedCount.value / total) * 100
})

const currentSectionHasFields = computed(() => {
  const fields = formStore.fieldsBySection[formStore.activeTab] || []
  return fields.length > 0
})

function sectionFields(section: string) {
  return formStore.fieldsBySection[section] || []
}

function sectionPendingCount(section: string) {
  const fields = formStore.fieldsBySection[section] || []
  return fields.filter(f => f.status === 'suggested' && f.value).length
}

function sectionComplete(section: string) {
  const fields = formStore.fieldsBySection[section] || []
  return fields.length > 0 && fields.every(f => f.status !== 'suggested')
}

function acceptSection(section: string) {
  const fields = formStore.fieldsBySection[section] || []
  for (const f of fields) {
    if (f.status === 'suggested' && f.value) {
      formStore.acceptField(f.fieldId)
    }
  }
}

function onViewSource(field: FormFieldType) {
  if (field.evidence) {
    highlightStore.highlightEvidence(field.evidence, field.criterionId)
  }
}
</script>

<style scoped>
.form-root {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.form-header {
  flex-shrink: 0;
  border-bottom: 1px solid #E0E0E0;
}

/* Custom tab bar — no overflow, always fits */
.tab-bar {
  display: flex;
  padding: 0 4px;
  gap: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.tab-bar::-webkit-scrollbar {
  display: none;
}
.tab-item {
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 10px 10px;
  font-size: 12px;
  font-weight: 500;
  color: #5F6368;
  border: none;
  background: none;
  cursor: pointer;
  white-space: nowrap;
  border-bottom: 2px solid transparent;
  transition: all 0.15s ease;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}
.tab-item:hover {
  color: #202124;
  background: #F8F9FA;
}
.tab-active {
  color: #1967D2;
  border-bottom-color: #1967D2;
}
.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #F9AB00;
  color: #fff;
  font-size: 9px;
  font-weight: 700;
  min-width: 14px;
  height: 14px;
  border-radius: 7px;
  padding: 0 3px;
}
.tab-check {
  color: #34A853;
  font-size: 11px;
  font-weight: 700;
}

.form-scroll {
  flex: 1 1 0;
  overflow-y: auto;
  min-height: 0;
}
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #FAFAFA;
  border-bottom: 1px solid #F1F3F4;
}
.section-content {
  padding: 8px 12px;
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
}

/* Bottom review bar */
.review-bar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #F8F9FA;
  border-top: 1px solid #E8EAED;
}
.review-progress {
  display: flex;
  align-items: center;
  gap: 8px;
}
.review-text {
  font-size: 11px;
  font-weight: 500;
  color: #5F6368;
}
.progress-track {
  width: 80px;
  height: 4px;
  border-radius: 2px;
  background: #E8EAED;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: #34A853;
  border-radius: 2px;
  transition: width 0.3s ease;
}
.review-chips {
  display: flex;
  gap: 6px;
}
.chip-accepted {
  font-size: 10px;
  font-weight: 500;
  color: #137333;
  background: #E6F4EA;
  padding: 2px 8px;
  border-radius: 10px;
}
.chip-pending {
  font-size: 10px;
  font-weight: 500;
  color: #B06000;
  background: #FEF7E0;
  padding: 2px 8px;
  border-radius: 10px;
}

.pdf-embed-container {
  min-height: 500px;
  height: calc(100vh - 200px);
}
.pdf-iframe {
  width: 100%;
  height: 100%;
  border: none;
}
</style>
