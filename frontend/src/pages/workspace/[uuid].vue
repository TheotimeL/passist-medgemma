<template>
  <div class="workspace-root">
    <!-- Top Bar -->
    <div class="workspace-topbar">
      <div class="topbar-left">
        <v-btn icon="mdi-arrow-left" variant="text" size="small" @click="$router.push('/')" />
        <span class="patient-name">{{ patientStore.fhirData?.name || 'Loading...' }}</span>
        <template v-if="extractionStore.policyStatus">
          <span class="criteria-chip" :class="'chip-' + statusKey">
            <v-icon size="12" class="mr-1">{{ statusIcon }}</v-icon>
            {{ extractionStore.effectiveMetCount }}/{{ extractionStore.policyStatus.total_count }} criteria
          </span>
          <span v-if="extractionStore.overrideCount > 0" class="override-chip">
            <v-icon size="10" class="mr-1">mdi-account-edit</v-icon>
            {{ extractionStore.overrideCount }} override{{ extractionStore.overrideCount > 1 ? 's' : '' }}
          </span>
        </template>
        <template v-else-if="extractionStore.isExtracting">
          <span class="criteria-chip chip-loading">
            <v-progress-circular indeterminate size="10" width="2" class="mr-1" />
            Analyzing...
          </span>
        </template>
      </div>
      <div class="topbar-right">
        <v-btn
          v-if="formStore.suggestedCount > 0"
          variant="outlined"
          color="success"
          size="small"
          class="mr-2"
          @click="formStore.acceptAll(); triggerPdfRefresh()"
        >
          Accept All ({{ formStore.suggestedCount }})
        </v-btn>
        <v-btn
          color="primary"
          size="small"
          :loading="generatingPdf"
          :disabled="formStore.acceptedCount === 0 && formStore.totalFilledCount === 0"
          @click="downloadPdf"
        >
          <v-icon start size="16">mdi-download</v-icon>
          Save PDF
        </v-btn>
      </div>
    </div>

    <!-- Extraction Progress -->
    <div v-if="extractionStore.isExtracting" class="extraction-progress">
      <v-progress-linear indeterminate color="primary" height="2" />
    </div>

    <!-- Main Content: Review Queue (left) + PDF Preview (right) -->
    <div class="workspace-content">
      <!-- Left Panel: Review Queue -->
      <div class="workspace-left">
        <ReviewQueue @field-accepted="onFieldAccepted" @view-source="onViewSource" />
      </div>

      <!-- Divider -->
      <div class="workspace-divider" />

      <!-- Right Panel: PDF or Notes -->
      <div class="workspace-right">
        <!-- Right Panel Header (mode toggle) -->
        <div class="right-header">
          <div class="right-tabs">
            <button
              class="right-tab"
              :class="{ 'right-tab-active': rightMode === 'pdf' }"
              @click="rightMode = 'pdf'"
            >
              <v-icon size="14" class="mr-1">mdi-file-pdf-box</v-icon>
              PA Form
            </button>
            <button
              class="right-tab"
              :class="{ 'right-tab-active': rightMode === 'notes' }"
              @click="rightMode = 'notes'"
            >
              <v-icon size="14" class="mr-1">mdi-file-document</v-icon>
              Clinical Notes
              <span v-if="highlightEvidence" class="notes-dot" />
            </button>
            <button
              v-if="extractionStore.results.length > 0 || extractionStore.complete"
              class="right-tab"
              :class="{ 'right-tab-active': rightMode === 'policy' }"
              @click="rightMode = 'policy'"
            >
              <v-icon size="14" class="mr-1">mdi-shield-check</v-icon>
              Policy
              <span v-if="extractionStore.treeEligibility === true" class="policy-dot policy-dot-pass" />
              <span v-else-if="extractionStore.treeEligibility === false" class="policy-dot policy-dot-fail" />
            </button>
            <button
              v-if="extractionStore.complete"
              class="right-tab"
              :class="{ 'right-tab-active': rightMode === 'justification' }"
              @click="rightMode = 'justification'"
            >
              <v-icon size="14" class="mr-1">mdi-file-document-edit</v-icon>
              Justification
              <span v-if="formStore.justificationLoading" class="justification-dot justification-dot-loading" />
              <span v-else-if="formStore.justificationText" class="justification-dot justification-dot-ready" />
            </button>
          </div>
          <div class="right-actions">
            <template v-if="rightMode === 'pdf'">
              <span v-if="pdfStale" class="stale-indicator">
                <v-icon size="12" color="warning" class="mr-1">mdi-alert-circle</v-icon>
                <span>Changed</span>
              </span>
              <v-btn
                variant="tonal"
                color="primary"
                size="x-small"
                :loading="generatingPreview"
                @click="generatePreview"
              >
                <v-icon start size="14">mdi-refresh</v-icon>
                {{ pdfPreviewUrl ? 'Refresh' : 'Generate' }}
              </v-btn>
            </template>
          </div>
        </div>

        <!-- PDF Mode -->
        <div v-if="rightMode === 'pdf'" class="right-body">
          <template v-if="pdfPreviewUrl">
            <iframe :src="pdfPreviewUrl + '#page=' + pdfPage" class="pdf-iframe" :key="pdfKey" />
          </template>
          <template v-else-if="generatingPreview">
            <div class="right-placeholder">
              <v-progress-circular indeterminate size="32" color="primary" />
              <span class="mt-3 text-caption text-medium-emphasis">Generating PDF...</span>
            </div>
          </template>
          <template v-else>
            <div class="right-placeholder">
              <v-icon size="56" color="grey-lighten-2">mdi-file-pdf-box</v-icon>
              <p class="text-body-2 text-medium-emphasis mt-3">
                Accept some fields to preview the filled form
              </p>
              <v-btn
                v-if="formStore.totalFilledCount > 0"
                variant="tonal"
                color="primary"
                size="small"
                class="mt-2"
                @click="generatePreview"
              >
                Generate Preview
              </v-btn>
            </div>
          </template>
        </div>

        <!-- Policy Mode -->
        <div v-show="rightMode === 'policy'" class="right-body policy-body">
          <PolicyTree @view-source="(ev: string, sn?: string) => onViewSource(ev, sn)" />
        </div>

        <!-- Justification Mode — split: criteria reference + editor -->
        <div v-if="rightMode === 'justification'" class="right-body justification-body">
          <!-- Criteria Reference Sidebar -->
          <div class="criteria-ref">
            <div class="criteria-ref-header">
              <v-icon size="14" class="mr-1">mdi-shield-check</v-icon>
              <span>Criteria</span>
              <span class="criteria-ref-count">
                {{ extractionStore.effectiveMetCount }}/{{ extractionStore.policyStatus?.total_count || '?' }}
              </span>
            </div>
            <div class="criteria-ref-list">
              <div
                v-for="item in criteriaList"
                :key="item.id"
                class="criteria-ref-item"
                :class="{ 'criteria-met': item.met, 'criteria-notmet': !item.met }"
              >
                <v-icon size="12" :color="item.met ? 'success' : 'error'">
                  {{ item.met ? 'mdi-check-circle' : 'mdi-close-circle' }}
                </v-icon>
                <span class="criteria-ref-name">{{ item.name }}</span>
              </div>
            </div>
          </div>

          <!-- Editor -->
          <div class="justification-editor">
            <div class="justification-toolbar">
              <span class="justification-title">Clinical Justification</span>
              <span v-if="formStore.justificationLoading" class="justification-status loading">
                <v-progress-circular indeterminate size="10" width="2" class="mr-1" />
                Generating...
              </span>
              <span v-else class="justification-status ready">Section VI</span>
            </div>
            <textarea
              v-model="formStore.justificationText"
              class="justification-textarea"
              placeholder="Clinical justification letter will be generated after extraction..."
              :disabled="formStore.justificationLoading"
            />
          </div>
        </div>

        <!-- Notes Mode -->
        <div v-if="rightMode === 'notes'" class="right-body notes-body">
          <NoteViewer
            v-if="patientStore.notes.length > 0"
            :notes="patientStore.notes"
            :selected-index="patientStore.selectedNoteIndex"
            :highlight-text="highlightEvidence"
            @update:selected-index="patientStore.selectedNoteIndex = $event"
          />
          <div v-else class="right-placeholder">
            <v-icon size="40" color="grey-lighten-2">mdi-file-document-outline</v-icon>
            <p class="text-body-2 text-medium-emphasis mt-3">No clinical notes available</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Error Snackbar -->
    <v-snackbar v-model="showError" color="error" timeout="6000" location="bottom right">
      {{ errorMessage }}
      <template #actions>
        <v-btn variant="text" @click="showError = false">Dismiss</v-btn>
      </template>
    </v-snackbar>

    <!-- Success Snackbar -->
    <v-snackbar v-model="showSuccess" color="success" timeout="3000" location="bottom right">
      {{ successMessage }}
    </v-snackbar>
  </div>
</template>

<script lang="ts" setup>
import { onMounted, ref, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { usePatientStore } from '@/stores/patient'
import { useExtractionStore } from '@/stores/extraction'
import { useFormStore } from '@/stores/form'
import ReviewQueue from '@/components/ReviewQueue.vue'
import NoteViewer from '@/components/NoteViewer.vue'
import PolicyTree from '@/components/PolicyTree.vue'

const route = useRoute()
const patientStore = usePatientStore()
const extractionStore = useExtractionStore()
const formStore = useFormStore()

// Flat criteria list for the justification reference sidebar
const criteriaList = computed(() => {
  const ps = extractionStore.policyStatus
  if (!ps) return []
  return ps.criteria.map((c: { id: string; name: string; status: string }) => {
    const override = extractionStore.overrides[c.id]
    const met = override ? override.met : c.status === 'met'
    const name = c.name
      .replace(/^\([^)]+\)\s*/, '')
      .replace(/^[ivx]+\.\s*/i, '')
    return { id: c.id, name, met }
  })
})

const generatingPdf = ref(false)
const generatingPreview = ref(false)
const showError = ref(false)
const errorMessage = ref('')
const showSuccess = ref(false)
const successMessage = ref('')
const pdfPreviewUrl = ref<string | null>(null)
const pdfStale = ref(false)
const rightMode = ref<'pdf' | 'notes' | 'policy' | 'justification'>('notes')
const highlightEvidence = ref<string | null>(null)
const pdfPage = ref(2) // Default to page 2 (actual form, not instructions)
const pdfKey = ref(0) // Force iframe re-render on page change
let refreshDebounce: ReturnType<typeof setTimeout> | null = null

// Map field sections to PDF pages
const sectionToPage: Record<string, number> = {
  demographics: 2,
  provider: 2,
  diagnosis: 2,
  step_therapy: 3,
  drug_request: 3,
  justification: 3,
}

const uuid = computed(() => route.params.uuid as string)

const statusKey = computed(() => {
  const ps = extractionStore.policyStatus
  if (!ps) return 'unknown'
  // Use tree evaluation (respects AND/OR logic + overrides) when available
  const treeResult = extractionStore.treeEligibility
  if (treeResult === true) return 'success'
  if (treeResult === false) return 'error'
  // Fallback to flat count when tree hasn't evaluated yet
  const effectiveMet = extractionStore.effectiveMetCount
  if (effectiveMet >= ps.total_count) return 'success'
  if (ps.overall === true) return 'success'
  if (ps.overall === false && extractionStore.overrideCount === 0) return 'error'
  return 'warning'
})

const statusIcon = computed(() => {
  const ps = extractionStore.policyStatus
  if (!ps) return 'mdi-help-circle'
  const treeResult = extractionStore.treeEligibility
  if (treeResult === true) return 'mdi-check-circle'
  if (treeResult === false) return 'mdi-close-circle'
  const effectiveMet = extractionStore.effectiveMetCount
  if (effectiveMet >= ps.total_count) return 'mdi-check-circle'
  if (ps.overall === true) return 'mdi-check-circle'
  if (ps.overall === false && extractionStore.overrideCount === 0) return 'mdi-close-circle'
  return 'mdi-clock-outline'
})

// Watch extraction errors
watch(() => extractionStore.error, (err) => {
  if (err) {
    errorMessage.value = err
    showError.value = true
  }
})

// Watch extraction results for progressive population
watch(() => extractionStore.results, (newResults) => {
  if (newResults.length > 0) {
    formStore.addExtractionResults(newResults)
  }
}, { deep: true })

// Watch extraction completion
watch(() => extractionStore.complete, (done) => {
  if (done && extractionStore.policyStatus) {
    const ps = extractionStore.policyStatus
    successMessage.value = `Extraction complete: ${ps.met_count}/${ps.total_count} criteria met`
    showSuccess.value = true
    // Fetch clinical justification after extraction, passing live results
    formStore.fetchJustification(uuid.value, extractionStore.results)
    // Auto-generate first PDF preview once extraction is done
    if (!pdfPreviewUrl.value && formStore.totalFilledCount > 0) {
      generatePreview()
    }
  }
})

onMounted(async () => {
  formStore.reset()
  extractionStore.reset()
  await patientStore.selectPatient(uuid.value)
  if (patientStore.fhirData) {
    formStore.buildFromFhir(patientStore.fhirData)
    formStore.addManualFields()
  }
  await extractionStore.fetchExtraction(uuid.value)
  // If extraction already completed (e.g. fast SSE), trigger justification fetch now
  if (extractionStore.complete && extractionStore.policyStatus && !formStore.justificationText) {
    formStore.fetchJustification(uuid.value, extractionStore.results)
  }
})

function onViewSource(evidence: string, sourceNote?: string) {
  if (sourceNote) {
    // Navigate to the specific note that contains this evidence
    patientStore.selectNoteByFilename(sourceNote)
  } else if (evidence && patientStore.notes.length > 1) {
    // Search all notes for the evidence text and select the matching one
    const evidenceNorm = evidence.toLowerCase().replace(/\s+/g, ' ').trim().slice(0, 80)
    const matchIdx = patientStore.notes.findIndex(note =>
      note.content.toLowerCase().replace(/\s+/g, ' ').includes(evidenceNorm)
    )
    if (matchIdx !== -1) {
      patientStore.selectedNoteIndex = matchIdx
    }
  }
  highlightEvidence.value = evidence
  rightMode.value = 'notes'
}

function onFieldAccepted(fieldIdOrSection: string) {
  pdfStale.value = true

  // Determine which PDF page to show based on the field's section
  // fieldIdOrSection can be either a field ID or a section name (from Accept All)
  const field = formStore.fields[fieldIdOrSection]
  const section = field ? field.section : fieldIdOrSection
  const targetPage = sectionToPage[section] || 2
  if (targetPage !== pdfPage.value) {
    pdfPage.value = targetPage
    pdfKey.value++
  }

  // Debounce PDF refresh — wait 1.5s after last change
  if (refreshDebounce) clearTimeout(refreshDebounce)
  refreshDebounce = setTimeout(() => {
    generatePreview()
  }, 1500)
}

function triggerPdfRefresh() {
  pdfStale.value = true
  if (refreshDebounce) clearTimeout(refreshDebounce)
  refreshDebounce = setTimeout(() => {
    generatePreview()
  }, 500)
}

async function generatePreview() {
  generatingPreview.value = true
  pdfStale.value = false
  try {
    const fieldUpdates = Object.values(formStore.fields)
      .filter(f => (f.status === 'accepted' || f.status === 'edited') && f.value)
      .map(f => ({ field_id: f.fieldId, value: f.value }))

    if (fieldUpdates.length === 0) {
      generatingPreview.value = false
      return
    }

    const res = await fetch('/api/form/generate-pdf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        uuid: uuid.value,
        fields: fieldUpdates,
        insurer: 'uhc',
        justification_text: formStore.justificationText || null,
      }),
    })

    if (res.ok) {
      if (pdfPreviewUrl.value) {
        URL.revokeObjectURL(pdfPreviewUrl.value)
      }
      const blob = await res.blob()
      pdfPreviewUrl.value = URL.createObjectURL(blob)
      pdfKey.value++ // Force iframe refresh
    } else {
      errorMessage.value = 'Failed to generate PDF preview.'
      showError.value = true
    }
  } catch {
    errorMessage.value = 'Network error generating PDF.'
    showError.value = true
  } finally {
    generatingPreview.value = false
  }
}

async function downloadPdf() {
  generatingPdf.value = true
  try {
    const fieldUpdates = Object.values(formStore.fields)
      .filter(f => (f.status === 'accepted' || f.status === 'edited') && f.value)
      .map(f => ({ field_id: f.fieldId, value: f.value }))

    const res = await fetch('/api/form/generate-pdf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        uuid: uuid.value,
        fields: fieldUpdates,
        insurer: 'uhc',
        justification_text: formStore.justificationText || null,
      }),
    })

    if (res.ok) {
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `pa_form_${patientStore.fhirData?.name?.replace(/\s+/g, '_') || 'form'}.pdf`
      a.click()
      URL.revokeObjectURL(url)
      successMessage.value = 'PDF downloaded'
      showSuccess.value = true
    } else {
      errorMessage.value = 'Failed to generate PDF.'
      showError.value = true
    }
  } catch {
    errorMessage.value = 'Network error.'
    showError.value = true
  } finally {
    generatingPdf.value = false
  }
}
</script>

<style scoped>
.workspace-root {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  background: #F8F9FA;
}

/* --- Top Bar --- */
.workspace-topbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 8px 0 4px;
  height: 48px;
  background: #fff;
  border-bottom: 1px solid #E0E0E0;
}
.topbar-left {
  display: flex;
  align-items: center;
  gap: 6px;
}
.patient-name {
  font-size: 15px;
  font-weight: 500;
  color: #202124;
}
.criteria-chip {
  display: inline-flex;
  align-items: center;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 12px;
}
.chip-success { color: #137333; background: #E6F4EA; }
.chip-error { color: #C5221F; background: #FCE8E6; }
.chip-warning { color: #B06000; background: #FEF7E0; }
.chip-loading { color: #1967D2; background: #E8F0FE; }
.override-chip {
  display: inline-flex;
  align-items: center;
  font-size: 10px;
  font-weight: 500;
  color: #7B1FA2;
  background: #F3E8FD;
  padding: 2px 6px;
  border-radius: 12px;
}
.topbar-right {
  display: flex;
  align-items: center;
}

.extraction-progress {
  flex-shrink: 0;
}

/* --- Content --- */
.workspace-content {
  flex: 1 1 0;
  display: flex;
  min-height: 0;
  overflow: hidden;
}
.workspace-left {
  width: 38%;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  background: #fff;
}
.workspace-divider {
  width: 1px;
  background: #E0E0E0;
  flex-shrink: 0;
}
.workspace-right {
  flex: 1 1 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

/* --- Right Panel --- */
.right-header {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 8px;
  background: #fff;
  border-bottom: 1px solid #E8EAED;
}
.right-tabs {
  display: flex;
  gap: 0;
}
.right-tab {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 500;
  color: #5F6368;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: all 0.15s ease;
}
.right-tab:hover {
  color: #202124;
  background: #F8F9FA;
}
.right-tab-active {
  color: #1967D2;
  border-bottom-color: #1967D2;
}
.notes-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #F9AB00;
  margin-left: 4px;
}
.policy-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-left: 4px;
}
.policy-dot-pass { background: #34A853; }
.policy-dot-fail { background: #EA4335; }
.right-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.stale-indicator {
  display: flex;
  align-items: center;
  font-size: 11px;
  color: #B06000;
}
.right-body {
  flex: 1 1 0;
  min-height: 0;
  overflow: hidden;
  background: #E8EAED;
}
.notes-body {
  background: #fff;
  padding: 16px;
  overflow-y: auto;
}
.policy-body {
  background: #fff;
  overflow-y: auto;
  padding: 8px;
}

/* Justification tab — split layout */
.justification-body {
  background: #fff;
  overflow: hidden;
  display: flex;
  flex-direction: row;
}

/* Criteria reference sidebar */
.criteria-ref {
  width: 240px;
  flex-shrink: 0;
  border-right: 1px solid #E8EAED;
  display: flex;
  flex-direction: column;
  background: #F8F9FA;
  overflow-y: auto;
}
.criteria-ref-header {
  display: flex;
  align-items: center;
  padding: 10px 10px 8px;
  font-size: 12px;
  font-weight: 600;
  color: #202124;
  border-bottom: 1px solid #E8EAED;
  gap: 4px;
}
.criteria-ref-count {
  margin-left: auto;
  font-size: 10px;
  font-weight: 500;
  color: #5F6368;
  background: #E8EAED;
  padding: 1px 6px;
  border-radius: 8px;
}
.criteria-ref-list {
  padding: 6px 0;
}
.criteria-ref-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 5px 10px;
  font-size: 11px;
  line-height: 1.3;
  color: #3C4043;
}
.criteria-ref-item.criteria-notmet {
  opacity: 0.5;
}
.criteria-ref-name {
  flex: 1;
  word-break: break-word;
}

/* Editor */
.justification-editor {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 10px 14px;
}
.justification-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.justification-title {
  font-size: 13px;
  font-weight: 600;
  color: #202124;
}
.justification-status {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 10px;
  display: flex;
  align-items: center;
}
.justification-status.loading {
  color: #1967D2;
  background: #E8F0FE;
}
.justification-status.ready {
  color: #137333;
  background: #E6F4EA;
}
.justification-textarea {
  flex: 1;
  width: 100%;
  font-size: 13px;
  font-family: inherit;
  line-height: 1.7;
  padding: 14px 16px;
  border: 1px solid #E0E0E0;
  border-radius: 8px;
  resize: none;
  outline: none;
  background: #FAFAFA;
  color: #202124;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.justification-textarea:focus {
  border-color: #4285F4;
  background: #fff;
  box-shadow: 0 0 0 2px rgba(66, 133, 244, 0.15);
}
.justification-textarea:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.justification-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-left: 4px;
}
.justification-dot-loading { background: #F9AB00; }
.justification-dot-ready { background: #34A853; }
.pdf-iframe {
  width: 100%;
  height: 100%;
  border: none;
}
.right-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  background: #F8F9FA;
}
</style>
