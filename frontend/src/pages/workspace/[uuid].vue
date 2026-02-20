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
          Save ({{ formStore.suggestedCount }})
        </v-btn>
        <v-btn
          color="primary"
          size="small"
          :loading="generatingPdf"
          :disabled="!pdfSaveEnabled"
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
      <!-- Left Panel: Review Queue / Notes -->
      <div class="workspace-left" :style="{ width: leftWidth + '%' }">
        <!-- Left Panel Tabs -->
        <div class="left-header">
          <div class="left-tabs">
            <button
              class="left-tab"
              :class="{ 'left-tab-active': leftMode === 'review' }"
              @click="leftMode = 'review'"
            >
              <v-icon size="14" class="mr-1">mdi-clipboard-check</v-icon>
              Review
              <span v-if="formStore.suggestedCount > 0 && leftMode !== 'review'" class="tab-pending-dot" />
            </button>
            <button
              class="left-tab"
              :class="{ 'left-tab-active': leftMode === 'notes' }"
              @click="leftMode = 'notes'"
            >
              <v-icon size="14" class="mr-1">mdi-file-document</v-icon>
              Clinical Notes
            </button>
          </div>
        </div>

        <!-- Left: Review Queue -->
        <div v-show="leftMode === 'review'" class="left-body">
          <template v-if="fhirLoading">
            <div class="ehr-loading">
              <div class="ehr-loading-header">
                <v-progress-circular indeterminate size="16" width="2" color="primary" class="mr-2" />
                <span class="ehr-loading-status">{{ fhirLoadingStatus }}</span>
              </div>
              <div class="ehr-skeleton">
                <div class="skeleton-section">
                  <div class="skeleton-title" style="width: 40%">&nbsp;</div>
                  <div class="skeleton-row" style="width: 70%">&nbsp;</div>
                  <div class="skeleton-row" style="width: 55%">&nbsp;</div>
                  <div class="skeleton-row" style="width: 65%">&nbsp;</div>
                  <div class="skeleton-row" style="width: 45%">&nbsp;</div>
                </div>
                <div class="skeleton-section">
                  <div class="skeleton-title" style="width: 35%">&nbsp;</div>
                  <div class="skeleton-row" style="width: 60%">&nbsp;</div>
                  <div class="skeleton-row" style="width: 50%">&nbsp;</div>
                </div>
                <div class="skeleton-section">
                  <div class="skeleton-title" style="width: 30%">&nbsp;</div>
                  <div class="skeleton-row" style="width: 75%">&nbsp;</div>
                  <div class="skeleton-row" style="width: 40%">&nbsp;</div>
                </div>
              </div>
            </div>
          </template>
          <ReviewQueue
            v-else
            ref="reviewQueueRef"
            @field-accepted="onFieldAccepted"
            @view-source="onViewSource"
          />
        </div>

        <!-- Left: Clinical Notes -->
        <div v-if="leftMode === 'notes'" class="left-body left-notes-body">
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

      <!-- Resizable Divider -->
      <div
        class="workspace-divider"
        :class="{ 'divider-dragging': isDragging }"
        @mousedown="startDrag"
      />

      <!-- Right Panel: PDF or Notes -->
      <div class="workspace-right">
        <!-- Right Panel Header (mode toggle) -->
        <div class="right-header">
          <div class="right-tabs">
            <button
              class="right-tab"
              :class="{ 'right-tab-active': rightMode === 'notes' }"
              @click="rightMode = 'notes'"
            >
              <v-icon size="14" class="mr-1">mdi-file-document</v-icon>
              Clinical Notes
            </button>
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
              :class="{ 'right-tab-active': rightMode === 'policy', 'right-tab-complete': extractionStore.allCriteriaReviewed }"
              @click="rightMode = 'policy'"
            >
              <v-icon size="14" class="mr-1">mdi-shield-check</v-icon>
              Policy
              <span v-if="extractionStore.allCriteriaReviewed" class="tab-complete-dot" />
              <span v-else-if="extractionStore.results.length > 0 && !extractionStore.allCriteriaReviewed" class="tab-pending-dot" />
            </button>
            <v-tooltip :text="!justificationTabAvailable ? 'Review all policy criteria first' : ''" location="bottom" :disabled="justificationTabAvailable">
              <template #activator="{ props: tooltipProps }">
                <button
                  v-bind="tooltipProps"
                  class="right-tab"
                  :class="{ 'right-tab-active': rightMode === 'justification', 'right-tab-disabled': !justificationTabAvailable, 'right-tab-complete': justificationVisited }"
                  :disabled="!justificationTabAvailable"
                  @click="rightMode = 'justification'; justificationVisited = true"
                >
                  <v-icon v-if="!justificationTabAvailable" size="12" class="mr-1">mdi-lock-outline</v-icon>
                  <v-icon v-else size="14" class="mr-1">mdi-file-document-edit</v-icon>
                  Justification
                  <span v-if="formStore.justificationLoading" class="justification-dot justification-dot-loading" />
                  <span v-else-if="justificationVisited && formStore.justificationText" class="tab-complete-dot" />
                  <span v-else-if="justificationTabAvailable && !justificationVisited" class="tab-pending-dot" />
                </button>
              </template>
            </v-tooltip>
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
                {{ pdfPreviewData ? 'Refresh' : 'Generate' }}
              </v-btn>
            </template>
          </div>
        </div>

        <!-- PDF Mode -->
        <div v-if="rightMode === 'pdf'" class="right-body">
          <template v-if="pdfPreviewData">
            <PdfViewer
              ref="pdfViewerRef"
              :pdf-data="pdfPreviewData"
              :field-id-to-pdf-name="fieldIdToPdfName"
              :field-statuses="formStore.fieldStatuses"
              @field-click="onPdfFieldClick"
            />
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
                Click "Generate Preview" to see the filled form
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
          <PolicyTree @view-source="(ev: string, sn?: string) => onPolicyViewSource(ev, sn)" />
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
import { onMounted, onUnmounted, ref, shallowRef, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { usePatientStore } from '@/stores/patient'
import { useExtractionStore } from '@/stores/extraction'
import { useFormStore } from '@/stores/form'
import ReviewQueue from '@/components/ReviewQueue.vue'
import NoteViewer from '@/components/NoteViewer.vue'
import PolicyTree from '@/components/PolicyTree.vue'
import PdfViewer from '@/components/PdfViewer.vue'

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
const pdfPreviewData = shallowRef<ArrayBuffer | null>(null)
const pdfStale = ref(false)
const rightMode = ref<'pdf' | 'notes' | 'policy' | 'justification'>('notes')
const leftMode = ref<'review' | 'notes'>('review')
const leftWidth = ref(38)
const isDragging = ref(false)
const fhirLoading = ref(true)
const fhirLoadingStatus = ref('Connecting to EHR...')
const highlightEvidence = ref<string | null>(null)
const justificationVisited = ref(false) // Track if justification tab was visited
const fieldIdToPdfName = ref<Record<string, string[]>>({})
const pdfViewerRef = ref<InstanceType<typeof PdfViewer> | null>(null)
const reviewQueueRef = ref<InstanceType<typeof ReviewQueue> | null>(null)
let refreshDebounce: ReturnType<typeof setTimeout> | null = null

// Tab availability rules
const justificationTabAvailable = computed(() => extractionStore.allCriteriaReviewed)
const pdfSaveEnabled = computed(() => justificationVisited.value && (formStore.acceptedCount > 0 || formStore.totalFilledCount > 0))

const uuid = computed(() => (route.params as { uuid: string }).uuid)

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

// Watch extraction completion — auto-switch to Policy tab
watch(() => extractionStore.complete, (done) => {
  if (done && extractionStore.policyStatus) {
    const ps = extractionStore.policyStatus
    successMessage.value = `Extraction complete — review ${ps.met_count} criteria to continue`
    showSuccess.value = true
    // Fetch clinical justification after extraction, passing live results
    formStore.fetchJustification(uuid.value, extractionStore.results)
    // Auto-generate first PDF preview once extraction is done
    if (!pdfPreviewData.value && formStore.totalFilledCount > 0) {
      generatePreview()
    }
  }
})

// Watch all criteria reviewed — unlock justification tab
watch(() => extractionStore.allCriteriaReviewed, (allDone) => {
  if (allDone) {
    successMessage.value = 'All criteria reviewed — review justification letter'
    showSuccess.value = true
    rightMode.value = 'justification'
    justificationVisited.value = true
  }
})

onMounted(async () => {
  formStore.reset()
  extractionStore.reset()
  fhirLoading.value = true
  fhirLoadingStatus.value = 'Connecting to EHR...'

  // Fetch field mapping in parallel with patient data
  fetch('/api/form/field-mapping')
    .then(r => r.ok ? r.json() : {})
    .then(data => { fieldIdToPdfName.value = data })
    .catch((e) => console.warn('Failed to fetch field mapping:', e))

  await patientStore.selectPatient(uuid.value)
  fhirLoadingStatus.value = 'Loading patient records...'
  await new Promise(resolve => setTimeout(resolve, 1500))
  fhirLoading.value = false
  if (patientStore.fhirData) {
    formStore.buildFromFhir(patientStore.fhirData)
    formStore.addManualFields()
    // Auto-generate PDF preview with FHIR fields so PDF shows demographics immediately
    generatePreview()
  }
  await extractionStore.fetchExtraction(uuid.value)
  // If extraction already completed (e.g. fast SSE), trigger justification fetch now
  if (extractionStore.complete && extractionStore.policyStatus && !formStore.justificationText) {
    formStore.fetchJustification(uuid.value, extractionStore.results)
  }
})

// --- Resizable divider ---
function startDrag(e: MouseEvent) {
  e.preventDefault()
  isDragging.value = true
  document.addEventListener('mousemove', onDrag)
  document.addEventListener('mouseup', stopDrag)
}

function onDrag(e: MouseEvent) {
  const container = document.querySelector('.workspace-content') as HTMLElement
  if (!container) return
  const rect = container.getBoundingClientRect()
  const pct = ((e.clientX - rect.left) / rect.width) * 100
  leftWidth.value = Math.min(70, Math.max(20, pct))
}

function stopDrag() {
  isDragging.value = false
  document.removeEventListener('mousemove', onDrag)
  document.removeEventListener('mouseup', stopDrag)
}

onUnmounted(() => {
  document.removeEventListener('mousemove', onDrag)
  document.removeEventListener('mouseup', stopDrag)
})

function navigateToNote(evidence: string, sourceNote?: string) {
  if (sourceNote) {
    patientStore.selectNoteByFilename(sourceNote)
  } else if (evidence && patientStore.notes.length > 1) {
    const evidenceNorm = evidence.toLowerCase().replace(/\s+/g, ' ').trim().slice(0, 80)
    const matchIdx = patientStore.notes.findIndex(note =>
      note.content.toLowerCase().replace(/\s+/g, ' ').includes(evidenceNorm)
    )
    if (matchIdx !== -1) {
      patientStore.selectedNoteIndex = matchIdx
    }
  }
  highlightEvidence.value = evidence
}

// ReviewQueue (left panel) → open notes in right panel
function onViewSource(evidence: string, sourceNote?: string) {
  navigateToNote(evidence, sourceNote)
  rightMode.value = 'notes'
}

// PolicyTree (right panel) → open notes in left panel
function onPolicyViewSource(evidence: string, sourceNote?: string) {
  navigateToNote(evidence, sourceNote)
  leftMode.value = 'notes'
}

// PDF → ReviewQueue: click a field overlay in PDF
function onPdfFieldClick(fieldId: string) {
  leftMode.value = 'review'
  reviewQueueRef.value?.focusField(fieldId)
}

function onFieldAccepted(fieldIdOrSection: string) {
  pdfStale.value = true

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
      .filter(f => f.status !== 'rejected' && f.value)
      .map(f => ({ field_id: f.fieldId, value: f.value, status: f.status }))

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
        insurer: 'bcbs',
        justification_text: formStore.justificationText || null,
      }),
    })

    if (res.ok) {
      const blob = await res.blob()
      pdfPreviewData.value = await blob.arrayBuffer()
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
      .filter(f => f.status !== 'rejected' && f.value)
      .map(f => ({ field_id: f.fieldId, value: f.value, status: f.status }))

    const res = await fetch('/api/form/generate-pdf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        uuid: uuid.value,
        fields: fieldUpdates,
        insurer: 'bcbs',
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
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  background: #fff;
}
.workspace-divider {
  width: 5px;
  background: #E0E0E0;
  flex-shrink: 0;
  cursor: col-resize;
  transition: background 0.15s;
}
.workspace-divider:hover,
.divider-dragging {
  background: #1967D2;
}
.workspace-right {
  flex: 1 1 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

/* --- Left Panel Tabs --- */
.left-header {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  padding: 0 8px;
  background: #fff;
  border-bottom: 1px solid #E8EAED;
}
.left-tabs {
  display: flex;
  gap: 0;
}
.left-tab {
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
.left-tab:hover {
  color: #202124;
  background: #F8F9FA;
}
.left-tab-active {
  color: #1967D2;
  border-bottom-color: #1967D2;
}
.left-body {
  flex: 1 1 0;
  min-height: 0;
  overflow-y: auto;
}
.left-notes-body {
  padding: 16px;
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
.right-tab-disabled {
  color: #BDC1C6 !important;
  cursor: not-allowed !important;
}
.right-tab-disabled:hover {
  background: none !important;
  color: #BDC1C6 !important;
}
.right-tab-complete {
  color: #137333;
}
.tab-complete-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #34A853;
  margin-left: 4px;
}
.tab-pending-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #F9AB00;
  margin-left: 4px;
}
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
.right-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  background: #F8F9FA;
}

/* EHR Loading State */
.ehr-loading {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 16px;
}
.ehr-loading-header {
  display: flex;
  align-items: center;
  padding: 8px 0 16px;
}
.ehr-loading-status {
  font-size: 13px;
  font-weight: 500;
  color: #1967D2;
}
.ehr-skeleton {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.skeleton-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 12px;
  background: #FAFAFA;
  border-radius: 6px;
}
.skeleton-title {
  height: 10px;
  background: #E0E0E0;
  border-radius: 3px;
  animation: skeleton-pulse 1.5s ease-in-out infinite;
}
.skeleton-row {
  height: 14px;
  background: #E8EAED;
  border-radius: 3px;
  animation: skeleton-pulse 1.5s ease-in-out infinite;
}
.skeleton-row:nth-child(odd) { animation-delay: 0.2s; }
.skeleton-row:nth-child(even) { animation-delay: 0.4s; }
@keyframes skeleton-pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}
</style>
