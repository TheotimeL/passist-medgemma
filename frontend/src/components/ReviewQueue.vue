<template>
  <div class="review-root">
    <!-- Progress Header -->
    <div class="review-header">
      <div class="progress-row">
        <span class="progress-label">
          {{ reviewedCount }}/{{ totalCount }} fields reviewed
        </span>
        <div class="progress-track">
          <div class="progress-fill" :style="{ width: progressPercent + '%' }" />
        </div>
      </div>
      <div class="progress-stats">
        <span v-if="formStore.acceptedCount > 0" class="stat stat-accepted">
          {{ formStore.acceptedCount }} accepted
        </span>
        <span v-if="editedCount > 0" class="stat stat-edited">
          {{ editedCount }} edited
        </span>
        <span v-if="rejectedCount > 0" class="stat stat-rejected">
          {{ rejectedCount }} rejected
        </span>
      </div>
    </div>

    <!-- Expand/Collapse All -->
    <div class="section-toolbar">
      <button class="toolbar-btn" @click="expandAll">
        <v-icon size="14" class="mr-1">mdi-unfold-more-horizontal</v-icon>
        Expand All
      </button>
      <button class="toolbar-btn" @click="collapseAll">
        <v-icon size="14" class="mr-1">mdi-unfold-less-horizontal</v-icon>
        Collapse All
      </button>
    </div>

    <!-- Scrollable Review Content -->
    <div class="review-scroll">
      <!-- Section Groups -->
      <div
        v-for="section in sections"
        :key="section.id"
        class="section-group"
      >
        <!-- Section Header -->
        <div class="section-header" @click="toggleSection(section.id)">
          <v-icon size="14" class="mr-1" :color="sectionIconColor(section.id)">
            {{ sectionCollapsed[section.id] ? 'mdi-chevron-right' : 'mdi-chevron-down' }}
          </v-icon>
          <span class="section-title">{{ section.label }}</span>
          <span class="section-count">{{ sectionFields(section.id).length }}</span>
          <div class="section-status">
            <span v-if="sectionPending(section.id) > 0" class="status-badge status-pending">
              {{ sectionPending(section.id) }} to review
            </span>
            <span v-else-if="sectionHasReviewed(section.id)" class="status-badge status-done">
              Done
            </span>
            <span v-else-if="sectionEmptyCount(section.id) > 0" class="status-badge status-empty">
              {{ sectionEmptyCount(section.id) }} to fill
            </span>
          </div>
          <span v-if="section.needsExtraction && extractionStore.isExtracting && sectionFields(section.id).length > 0" class="ai-pending-badge">
            <v-icon size="10" class="mr-1">mdi-loading mdi-spin</v-icon>
            AI updating
          </span>
          <button
            v-if="sectionPending(section.id) > 0"
            class="save-section-btn"
            @click.stop="acceptSection(section.id)"
          >
            Save{{ section.needsExtraction && extractionStore.isExtracting ? ' Current' : '' }}
          </button>
        </div>

        <!-- COMPACT TABLE VIEW for EHR sections -->
        <div v-if="!sectionCollapsed[section.id] && section.compact" class="compact-table">
          <div
            v-for="field in sectionFields(section.id)"
            :key="field.fieldId"
            :data-field-id="field.fieldId"
            class="compact-row"
            :class="[compactRowClass(field), { 'field-flash': flashFieldId === field.fieldId }]"
          >
            <span class="compact-label" :class="{ 'compact-label-required': field.required }">{{ field.label }}</span>

            <!-- Editing inline -->
            <template v-if="editingField === field.fieldId">
              <input
                :ref="autoFocus"
                v-model="editValue"
                class="compact-edit-input"
                @keyup.enter="saveEdit(field.fieldId)"
                @keyup.escape="cancelEdit()"
                @blur="saveEdit(field.fieldId)"
              />
            </template>

            <!-- Normal display -->
            <template v-else>
              <template v-if="field.value">
                <span class="compact-value compact-clickable-value" @click.stop="startEdit(field)">{{ field.value }}</span>
                <span class="source-chip" :class="'source-' + field.source">{{ sourceLabel(field.source) }}</span>
                <button class="locate-pdf-btn" @click.stop="emit('locateInPdf', field.fieldId)" title="Locate in PDF">
                  <v-icon size="12">mdi-file-find-outline</v-icon>
                </button>
              </template>
              <template v-else-if="manualEntryField === field.fieldId">
                <input
                  :ref="autoFocus"
                  v-model="manualEntryValue"
                  class="compact-edit-input"
                  :placeholder="'Enter ' + field.label.toLowerCase()"
                  @keyup.enter="saveManualEntry(field.fieldId)"
                  @keyup.escape="cancelManualEntry()"
                  @blur="saveManualEntry(field.fieldId)"
                />
              </template>
              <template v-else>
                <span
                  v-if="field.required"
                  class="compact-value compact-empty-required"
                  @click.stop="startManualEntry(field)"
                >Enter {{ field.label.toLowerCase() }}</span>
                <span v-else class="compact-value compact-empty compact-clickable" @click.stop="startManualEntry(field)">—</span>
              </template>
            </template>
          </div>
        </div>

        <!-- DRUG HISTORY TABLE VIEW -->
        <div v-if="!sectionCollapsed[section.id] && section.table" class="drug-table-wrap">
          <table v-if="fieldGroups(section.id).length > 0" class="drug-table">
            <thead>
              <tr>
                <th>Drug</th>
                <th>Dose</th>
                <th>Dates</th>
                <th>Failure Reason</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="group in fieldGroups(section.id)"
                :key="group.key"
                :data-field-id="tableFieldId(group, 'name')"
                class="drug-table-row"
                :class="{ 'drug-table-row-reviewed': groupIsReviewed(group) }"
              >
                <td v-for="col in ['name', 'dose', 'dates', 'reason']" :key="col" class="drug-table-cell">
                  <!-- Inline edit (existing value) -->
                  <template v-if="editingField === tableFieldId(group, col)">
                    <input
                      :ref="autoFocus"
                      v-model="editValue"
                      class="compact-edit-input"
                      @keyup.enter="saveEdit(tableFieldId(group, col))"
                      @keyup.escape="cancelEdit()"
                      @blur="saveEdit(tableFieldId(group, col))"
                    />
                  </template>
                  <!-- Manual entry (empty cell) -->
                  <template v-else-if="manualEntryField === tableFieldId(group, col)">
                    <input
                      :ref="autoFocus"
                      v-model="manualEntryValue"
                      class="compact-edit-input"
                      @keyup.enter="saveManualEntry(tableFieldId(group, col))"
                      @keyup.escape="cancelManualEntry()"
                      @blur="saveManualEntry(tableFieldId(group, col))"
                    />
                  </template>
                  <!-- Display -->
                  <template v-else>
                    <span
                      v-if="tableFieldValue(group, col)"
                      class="drug-table-value compact-clickable-value"
                      @click.stop="startEdit(tableField(group, col)!)"
                    >{{ tableFieldValue(group, col) }}</span>
                    <span
                      v-else-if="tableField(group, col)"
                      class="drug-table-empty compact-clickable"
                      @click.stop="startManualEntry(tableField(group, col)!)"
                    >—</span>
                    <span v-else class="drug-table-empty">—</span>
                  </template>
                </td>
                <td class="drug-table-actions">
                  <span v-if="groupPending(group) > 0" class="source-chip source-llm">AI</span>
                  <button
                    v-if="group.evidence && groupPending(group) > 0"
                    class="action-link"
                    @click="emit('viewSource', group.evidence!, group.sourceNote)"
                    title="View in Notes"
                  >
                    <v-icon size="12">mdi-text-search</v-icon>
                  </button>
                  <button class="locate-pdf-btn" @click.stop="emit('locateInPdf', tableFieldId(group, 'name'))" title="Locate in PDF">
                    <v-icon size="12">mdi-file-find-outline</v-icon>
                  </button>
                  <button v-if="groupIsReviewed(group)" class="undo-btn drug-table-undo" @click="undoGroup(group)">undo</button>
                  <button class="btn-remove" @click="removeGroup(group)" title="Remove drug">
                    <v-icon size="12">mdi-delete-outline</v-icon>
                  </button>
                </td>
              </tr>
            </tbody>
          </table>

          <!-- Skeleton during extraction -->
          <div v-if="fieldGroups(section.id).length === 0 && extractionStore.isExtracting && section.needsExtraction" class="drug-table-skeleton">
            <div class="skeleton-line" style="width: 100%">&nbsp;</div>
            <div class="skeleton-line" style="width: 90%">&nbsp;</div>
          </div>

          <!-- Empty state -->
          <div v-if="fieldGroups(section.id).length === 0 && !extractionStore.isExtracting" class="drug-empty">
            <v-icon size="16" color="grey" class="mr-1">mdi-information-outline</v-icon>
            <span>No drug history extracted</span>
          </div>

          <!-- Add Prior Drug button -->
          <button
            v-if="!extractionStore.isExtracting"
            class="add-drug-btn"
            @click="addManualDrugEntry(section.id)"
          >
            <v-icon size="14" class="mr-1">mdi-plus-circle-outline</v-icon>
            Add Prior Drug
          </button>
        </div>

        <!-- GROUPED DRUG VIEW for drug sections -->
        <div v-if="!sectionCollapsed[section.id] && section.grouped" class="section-fields drug-section">
          <!-- Existing drug groups -->
          <div
            v-for="group in fieldGroups(section.id)"
            :key="group.key"
            class="drug-group"
            :class="{'drug-group-reviewed': groupIsReviewed(group), 'drug-group-empty': groupAllEmpty(group)}"
          >
            <div class="drug-group-header">
              <span class="drug-group-name">{{ group.drugName }}</span>
              <span v-if="groupPending(group) > 0" class="source-chip source-llm">AI</span>
              <div class="drug-group-actions">
                <button v-if="groupIsReviewed(group)" class="undo-btn drug-undo" @click="undoGroup(group)">undo</button>
                <button class="btn-remove drug-btn" @click="removeGroup(group)" title="Remove drug">
                  <v-icon size="12">mdi-delete-outline</v-icon>
                </button>
              </div>
            </div>
            <div class="drug-group-fields">
              <div
                v-for="field in group.fields"
                :key="field.fieldId"
                class="drug-field-row"
              >
                <span class="drug-field-label">{{ field.label }}</span>
                <template v-if="editingField === field.fieldId">
                  <input
                    :ref="autoFocus"
                    v-model="editValue"
                    class="compact-edit-input"
                    @keyup.enter="saveEdit(field.fieldId)"
                    @keyup.escape="cancelEdit()"
                    @blur="saveEdit(field.fieldId)"
                  />
                </template>
                <template v-else>
                  <template v-if="field.value">
                    <span class="drug-field-value compact-clickable-value" @click.stop="startEdit(field)">{{ field.value }}</span>
                  </template>
                  <template v-else-if="manualEntryField === field.fieldId">
                    <input
                      :ref="autoFocus"
                      v-model="manualEntryValue"
                      class="compact-edit-input"
                      :placeholder="'Enter ' + field.label.toLowerCase()"
                      @keyup.enter="saveManualEntry(field.fieldId)"
                      @keyup.escape="cancelManualEntry()"
                      @blur="saveManualEntry(field.fieldId)"
                    />
                  </template>
                  <template v-else>
                    <span class="drug-field-value compact-empty">—</span>
                    <button class="add-manually-btn" @click="startManualEntry(field)">
                      <v-icon size="11" class="mr-1">mdi-plus</v-icon> Add
                    </button>
                  </template>
                </template>
              </div>
            </div>
            <div v-if="group.evidence && groupPending(group) > 0" class="drug-group-footer">
              <button class="action-link" @click="emit('viewSource', group.evidence!, group.sourceNote)">
                <v-icon size="12" class="mr-1">mdi-text-search</v-icon>
                View in Notes
              </button>
            </div>
          </div>

          <!-- Empty state + Add button -->
          <div v-if="fieldGroups(section.id).length === 0 && !extractionStore.isExtracting" class="drug-empty">
            <v-icon size="16" color="grey" class="mr-1">mdi-information-outline</v-icon>
            <span>No {{ section.label.toLowerCase() }} extracted</span>
          </div>

          <!-- Add entry button -->
          <button
            v-if="!extractionStore.isExtracting"
            class="add-drug-btn"
            @click="addManualDrugEntry(section.id)"
          >
            <v-icon size="14" class="mr-1">mdi-plus-circle-outline</v-icon>
            Add {{ section.id === 'step_therapy' ? 'Prior Drug' : 'Drug' }}
          </button>
        </div>

        <!-- CARD VIEW for AI-extracted sections (need more review) -->
        <div v-if="!sectionCollapsed[section.id] && !section.compact && !section.grouped && !section.table" class="section-fields">
          <div
            v-for="field in sectionFields(section.id)"
            :key="field.fieldId"
            :data-field-id="field.fieldId"
            class="review-item"
            :class="[itemClass(field), { 'field-flash': flashFieldId === field.fieldId }]"
          >
            <!-- Accepted/Edited/Rejected: compact row -->
            <template v-if="field.status !== 'suggested'">
              <div class="item-compact">
                <span class="item-label-sm">{{ field.label }}</span>
                <span class="item-value-sm compact-clickable-value" :class="{ 'text-decoration-line-through text-disabled': field.status === 'rejected' }" @click.stop="startEdit(field)">
                  {{ field.value || field.originalValue }}
                </span>
                <button class="undo-btn" @click="undoField(field)">undo</button>
              </div>
            </template>

            <!-- Pending: expanded review card -->
            <template v-else-if="field.value">
              <div class="item-pending">
                <div class="item-top">
                  <span class="item-label" :class="{ 'compact-label-required': field.required }">{{ field.label }}</span>
                  <span class="source-chip" :class="'source-' + field.source">
                    {{ sourceLabel(field.source) }}
                  </span>
                  <button class="locate-pdf-btn item-top-link" @click.stop="emit('locateInPdf', field.fieldId)" title="Locate in PDF">
                    <v-icon size="12">mdi-file-find-outline</v-icon>
                  </button>
                  <button v-if="field.evidence" class="action-link item-top-link" @click.stop="toggleEvidence(field)">
                    {{ expandedEvidence === field.fieldId ? 'Hide Source' : 'View Source' }}
                  </button>
                </div>
                <div class="item-value compact-clickable-value" @click.stop="startEdit(field)">{{ field.value }}</div>

                <!-- Inline Evidence (expandable) -->
                <div v-if="field.evidence && expandedEvidence === field.fieldId" class="evidence-box">
                  <div class="evidence-header">
                    <v-icon size="12" color="primary" class="mr-1">mdi-text-search</v-icon>
                    <span>Source Evidence</span>
                    <button class="evidence-close" @click="expandedEvidence = null">
                      <v-icon size="14">mdi-close</v-icon>
                    </button>
                  </div>
                  <p class="evidence-text">{{ field.evidence }}</p>
                </div>

                <!-- Inline Edit -->
                <div v-if="editingField === field.fieldId" class="edit-row">
                  <input
                    :ref="autoFocus"
                    v-model="editValue"
                    class="edit-input"
                    @keyup.enter="saveEdit(field.fieldId)"
                    @keyup.escape="cancelEdit()"
                    @blur="saveEdit(field.fieldId)"
                  />
                </div>
              </div>
            </template>

            <!-- Empty: no value — offer manual entry -->
            <template v-else>
              <div class="item-compact item-empty">
                <v-icon size="14" color="grey" class="flex-shrink-0">mdi-minus-circle-outline</v-icon>
                <span class="item-label-sm">{{ field.label }}</span>
                <template v-if="manualEntryField === field.fieldId">
                  <input
                    :ref="autoFocus"
                    v-model="manualEntryValue"
                    class="edit-input manual-inline-input"
                    :placeholder="'Enter ' + field.label.toLowerCase()"
                    @keyup.enter="saveManualEntry(field.fieldId)"
                    @keyup.escape="cancelManualEntry()"
                    @blur="saveManualEntry(field.fieldId)"
                  />
                </template>
                <template v-else>
                  <button class="add-manually-btn" @click="startManualEntry(field)">
                    <v-icon size="11" class="mr-1">mdi-plus</v-icon> Add
                  </button>
                </template>
              </div>
            </template>
          </div>
        </div>

        <!-- Skeleton for sections still loading (non-table sections only; table has its own) -->
        <div v-if="!sectionCollapsed[section.id] && !section.table && sectionFields(section.id).length === 0 && extractionStore.isExtracting && section.needsExtraction" class="section-fields">
          <div class="review-item">
            <div class="skeleton-line" style="width: 60%">&nbsp;</div>
            <div class="skeleton-line" style="width: 80%">&nbsp;</div>
          </div>
        </div>
      </div>

    </div>

    <!-- Add Drug Dialog -->
    <v-dialog v-model="showAddDrugDialog" max-width="420">
      <v-card>
        <v-card-title class="text-subtitle-1">
          Add {{ addDrugTargetSection === 'step_therapy' ? 'Prior Drug' : 'Drug Request' }}
        </v-card-title>
        <v-card-text class="pb-2">
          <v-text-field
            v-model="addDrugForm.name"
            label="Drug Name *"
            density="compact"
            variant="outlined"
            class="mb-2"
            hide-details
          />
          <v-text-field
            v-model="addDrugForm.dose"
            label="Dose"
            density="compact"
            variant="outlined"
            class="mb-2"
            hide-details
          />
          <template v-if="addDrugTargetSection === 'step_therapy'">
            <v-text-field
              v-model="addDrugForm.dates"
              label="Dates"
              density="compact"
              variant="outlined"
              class="mb-2"
              hide-details
            />
            <v-text-field
              v-model="addDrugForm.failureReason"
              label="Failure Reason"
              density="compact"
              variant="outlined"
              hide-details
            />
          </template>
          <template v-else>
            <v-text-field
              v-model="addDrugForm.quantity"
              label="Quantity"
              density="compact"
              variant="outlined"
              class="mb-2"
              hide-details
            />
            <v-text-field
              v-model="addDrugForm.daysSupply"
              label="Days Supply"
              density="compact"
              variant="outlined"
              class="mb-2"
              hide-details
            />
            <v-text-field
              v-model="addDrugForm.route"
              label="Route of Administration"
              density="compact"
              variant="outlined"
              class="mb-2"
              hide-details
            />
            <v-text-field
              v-model="addDrugForm.duration"
              label="Expected Therapy Duration"
              density="compact"
              variant="outlined"
              class="mb-2"
              hide-details
            />
            <v-text-field
              v-model="addDrugForm.hcpcs"
              label="HCPCS / J-Code"
              density="compact"
              variant="outlined"
              hide-details
            />
          </template>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="showAddDrugDialog = false">Cancel</v-btn>
          <v-btn color="primary" variant="flat" :disabled="!addDrugForm.name.trim()" @click="submitAddDrug">Add</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script lang="ts" setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useFormStore, type FormField } from '@/stores/form'
import { useExtractionStore } from '@/stores/extraction'

const emit = defineEmits<{
  fieldAccepted: [fieldId: string]
  viewSource: [evidence: string, sourceNote?: string]
  locateInPdf: [fieldId: string]
}>()

const formStore = useFormStore()
const extractionStore = useExtractionStore()

const sectionCollapsed = ref<Record<string, boolean>>({})
const expandedEvidence = ref<string | null>(null)
const editingField = ref<string | null>(null)
const editValue = ref('')
const manualEntryField = ref<string | null>(null)
const manualEntryValue = ref('')

// Auto-focus callback: used as :ref on inputs so they focus immediately when mounted
function autoFocus(el: any) {
  if (el instanceof HTMLInputElement) el.focus()
}

// Add Drug Dialog state
const showAddDrugDialog = ref(false)
const addDrugTargetSection = ref<'step_therapy' | 'drug_request'>('step_therapy')
const addDrugForm = ref({
  name: '',
  dose: '',
  dates: '',
  failureReason: '',
  quantity: '',
  daysSupply: '',
  route: '',
  duration: '',
  hcpcs: '',
})

// Auto-collapse sections where all fields are reviewed, reopen when new AI fields arrive
watch(() => formStore.fieldsBySection, () => {
  for (const section of sections) {
    const fields = sectionFields(section.id)
    const pending = sectionPending(section.id)
    if (fields.length > 0 && pending === 0) {
      if (sectionCollapsed.value[section.id] === undefined) {
        sectionCollapsed.value[section.id] = true
      }
    } else if (pending > 0 && sectionCollapsed.value[section.id] === true) {
      // New pending fields arrived (e.g. AI updated diagnosis) — reopen so doctor sees them
      sectionCollapsed.value[section.id] = false
    }
  }
}, { deep: true })

// compact: true = show as table (EHR data, trusted)
// compact: false = show as cards (AI-extracted, needs review)
// grouped: true = show as grouped drug cards (related fields together)
const sections = [
  { id: 'demographics', label: 'Patient Info', needsExtraction: false, compact: true, grouped: false, table: false },
  { id: 'provider', label: 'Provider', needsExtraction: true, compact: true, grouped: false, table: false },
  { id: 'diagnosis', label: 'Diagnosis', needsExtraction: true, compact: true, grouped: false, table: false },
  { id: 'step_therapy', label: 'Drug History', needsExtraction: true, compact: false, grouped: false, table: true },
  { id: 'drug_request', label: 'Drug Request', needsExtraction: true, compact: true, grouped: false, table: false },
]

interface DrugGroup {
  key: string
  drugName: string
  fields: FormField[]
  evidence?: string
  sourceNote?: string
}

function fieldGroups(sectionId: string): DrugGroup[] {
  const fields = sectionFields(sectionId)
  if (fields.length === 0) return []

  if (sectionId === 'drug_request') {
    // All drug_request fields in one card
    const groups: DrugGroup[] = []

    if (fields.length > 0) {
      const nameField = fields.find(f => f.label.toLowerCase().includes('drug') && !f.label.toLowerCase().includes('duration'))
      groups.push({
        key: 'drug_request',
        drugName: nameField?.value || 'Drug Request',
        fields,
        evidence: fields.find(f => f.evidence)?.evidence,
        sourceNote: fields.find(f => f.sourceNote)?.sourceNote,
      })
    }

    return groups
  }

  // Group by prefix (strip field suffix)
  const groupMap: Record<string, FormField[]> = {}
  const groupOrder: string[] = []
  for (const f of fields) {
    const key = f.fieldId.replace(/_(name|dates|reason|dose|quantity|days_supply|route|duration|hcpcs)$/, '')
    if (!groupMap[key]) {
      groupMap[key] = []
      groupOrder.push(key)
    }
    groupMap[key].push(f)
  }

  return groupOrder.map(key => {
    const groupFields = groupMap[key]
    const nameField = groupFields.find(f => f.fieldId.endsWith('_name') || f.label === 'Prior Drug')
    return {
      key,
      drugName: nameField?.value || key.replace(/^prior_drug_/, '').replace(/_/g, ' '),
      fields: groupFields,
      evidence: groupFields[0]?.evidence,
      sourceNote: groupFields[0]?.sourceNote,
    }
  })
}

function groupPending(group: DrugGroup): number {
  return group.fields.filter(f => f.status === 'suggested' && f.value).length
}

function groupAllEmpty(group: DrugGroup): boolean {
  return group.fields.every(f => !f.value)
}

function groupIsReviewed(group: DrugGroup): boolean {
  return groupPending(group) === 0 && group.fields.some(f => f.status !== 'suggested')
}

function acceptGroup(group: DrugGroup) {
  for (const f of group.fields) {
    if (f.status === 'suggested' && f.value) {
      formStore.acceptField(f.fieldId)
    }
  }
  emit('fieldAccepted', group.key)
}

function rejectGroup(group: DrugGroup) {
  for (const f of group.fields) {
    if (f.status === 'suggested' && f.value) {
      formStore.rejectField(f.fieldId)
    }
  }
}

function undoGroup(group: DrugGroup) {
  for (const f of group.fields) {
    formStore.undoField(f.fieldId)
  }
}

function removeGroup(group: DrugGroup) {
  for (const f of group.fields) {
    formStore.removeField(f.fieldId)
  }
}

// Table view helpers — map column names to field suffixes
const colSuffixMap: Record<string, string[]> = {
  name: ['_name'],
  dose: ['_dose'],
  dates: ['_dates'],
  reason: ['_reason'],
}

function tableField(group: DrugGroup, col: string): FormField | undefined {
  const suffixes = colSuffixMap[col] || [`_${col}`]
  return group.fields.find(f => suffixes.some(s => f.fieldId.endsWith(s)))
}

function tableFieldId(group: DrugGroup, col: string): string {
  return tableField(group, col)?.fieldId ?? ''
}

function tableFieldValue(group: DrugGroup, col: string): string {
  return tableField(group, col)?.value ?? ''
}

let manualDrugCounter = 0

function addManualDrugEntry(sectionId: string) {
  addDrugTargetSection.value = sectionId as 'step_therapy' | 'drug_request'
  addDrugForm.value = { name: '', dose: '', dates: '', failureReason: '', quantity: '', daysSupply: '', route: '', duration: '', hcpcs: '' }
  showAddDrugDialog.value = true
}

function submitAddDrug() {
  const form = addDrugForm.value
  const section = addDrugTargetSection.value

  if (section === 'step_therapy') {
    manualDrugCounter++
    const prefix = `manual_prior_drug_${manualDrugCounter}`
    formStore.addManualDrug(prefix, 'step_therapy', [
      { suffix: '_name', label: 'Prior Drug' },
      { suffix: '_dates', label: 'Dates' },
      { suffix: '_reason', label: 'Failure Reason' },
      { suffix: '_dose', label: 'Dose' },
    ])
    if (form.name.trim()) formStore.editField(`${prefix}_name`, form.name.trim())
    if (form.dose.trim()) formStore.editField(`${prefix}_dose`, form.dose.trim())
    if (form.dates.trim()) formStore.editField(`${prefix}_dates`, form.dates.trim())
    if (form.failureReason.trim()) formStore.editField(`${prefix}_reason`, form.failureReason.trim())
  } else {
    const hasRequestedDrug = !!formStore.fields['requested_drug']
    let prefix: string
    if (!hasRequestedDrug) {
      prefix = 'requested'
      formStore.addManualDrug(prefix, 'drug_request', [
        { suffix: '_drug', label: 'Requested Drug' },
        { suffix: '_dose', label: 'Drug Dose' },
      ])
      if (form.name.trim()) formStore.editField(`${prefix}_drug`, form.name.trim())
      if (form.dose.trim()) formStore.editField(`${prefix}_dose`, form.dose.trim())
    } else {
      manualDrugCounter++
      prefix = `manual_drug_${manualDrugCounter}`
      formStore.addManualDrug(prefix, 'drug_request', [
        { suffix: '_name', label: 'Requested Drug' },
        { suffix: '_dose', label: 'Drug Dose' },
        { suffix: '_quantity', label: 'Quantity' },
        { suffix: '_days_supply', label: 'Days Supply' },
        { suffix: '_route', label: 'Route of Administration' },
        { suffix: '_duration', label: 'Expected Therapy Duration' },
        { suffix: '_hcpcs', label: 'HCPCS / J-Code' },
      ])
      if (form.name.trim()) formStore.editField(`${prefix}_name`, form.name.trim())
      if (form.dose.trim()) formStore.editField(`${prefix}_dose`, form.dose.trim())
      if (form.quantity.trim()) formStore.editField(`${prefix}_quantity`, form.quantity.trim())
      if (form.daysSupply.trim()) formStore.editField(`${prefix}_days_supply`, form.daysSupply.trim())
      if (form.route.trim()) formStore.editField(`${prefix}_route`, form.route.trim())
      if (form.duration.trim()) formStore.editField(`${prefix}_duration`, form.duration.trim())
      if (form.hcpcs.trim()) formStore.editField(`${prefix}_hcpcs`, form.hcpcs.trim())
    }
    // Also populate the standalone fields if they exist and are empty
    if (form.quantity.trim() && formStore.fields['quantity'] && !formStore.fields['quantity'].value) {
      formStore.editField('quantity', form.quantity.trim())
    }
    if (form.daysSupply.trim() && formStore.fields['days_supply'] && !formStore.fields['days_supply'].value) {
      formStore.editField('days_supply', form.daysSupply.trim())
    }
    if (form.route.trim() && formStore.fields['route_of_admin'] && !formStore.fields['route_of_admin'].value) {
      formStore.editField('route_of_admin', form.route.trim())
    }
    if (form.duration.trim() && formStore.fields['therapy_duration'] && !formStore.fields['therapy_duration'].value) {
      formStore.editField('therapy_duration', form.duration.trim())
    }
    if (form.hcpcs.trim() && formStore.fields['hcpcs_code'] && !formStore.fields['hcpcs_code'].value) {
      formStore.editField('hcpcs_code', form.hcpcs.trim())
    }
  }

  showAddDrugDialog.value = false
}

const totalCount = computed(() => Object.values(formStore.fields).filter(f => f.value).length)
const reviewedCount = computed(() =>
  Object.values(formStore.fields).filter(f => f.status !== 'suggested').length
)
const editedCount = computed(() =>
  Object.values(formStore.fields).filter(f => f.status === 'edited').length
)
const rejectedCount = computed(() =>
  Object.values(formStore.fields).filter(f => f.status === 'rejected').length
)
const progressPercent = computed(() => {
  if (totalCount.value === 0) return 0
  return (reviewedCount.value / totalCount.value) * 100
})

function sectionFields(section: string) {
  return formStore.fieldsBySection[section] || []
}

function sectionPending(section: string) {
  return (formStore.fieldsBySection[section] || []).filter(f => f.status === 'suggested' && f.value).length
}

function sectionHasReviewed(section: string) {
  const fields = sectionFields(section)
  return fields.some(f => f.status !== 'suggested') && fields.filter(f => f.value).length > 0
}

function sectionEmptyCount(section: string) {
  return (formStore.fieldsBySection[section] || []).filter(f => !f.value && f.status === 'suggested').length
}

function sectionIconColor(section: string) {
  if (sectionPending(section) > 0) return 'warning'
  if (sectionFields(section).length > 0) return 'success'
  return 'grey'
}

function toggleSection(id: string) {
  sectionCollapsed.value[id] = !sectionCollapsed.value[id]
}

function expandAll() {
  for (const section of sections) {
    sectionCollapsed.value[section.id] = false
  }
}

function collapseAll() {
  for (const section of sections) {
    sectionCollapsed.value[section.id] = true
  }
}

function acceptSection(section: string) {
  const fields = formStore.fieldsBySection[section] || []
  for (const f of fields) {
    if (f.status === 'suggested' && f.value) {
      formStore.acceptField(f.fieldId)
    }
  }
  emit('fieldAccepted', section)
}

function acceptAndNotify(fieldId: string) {
  formStore.acceptField(fieldId)
  emit('fieldAccepted', fieldId)
}

function undoField(field: FormField) {
  formStore.undoField(field.fieldId)
}

function compactRowClass(field: FormField) {
  if (field.status === 'accepted') return 'compact-accepted'
  if (field.status === 'edited') return 'compact-edited'
  if (field.status === 'rejected') return 'compact-rejected'
  return ''
}

function itemClass(field: FormField) {
  if (field.status === 'accepted') return 'item-done'
  if (field.status === 'rejected') return 'item-rejected-row'
  if (field.status === 'edited') return 'item-edited-row'
  if (!field.value) return 'item-empty-row'
  return ''
}

function statusColor(field: FormField) {
  switch (field.status) {
    case 'accepted': return 'success'
    case 'rejected': return 'error'
    case 'edited': return 'info'
    default: return 'grey'
  }
}

function statusIcon(field: FormField) {
  switch (field.status) {
    case 'accepted': return 'mdi-check-circle'
    case 'rejected': return 'mdi-close-circle'
    case 'edited': return 'mdi-pencil-circle'
    default: return 'mdi-circle-outline'
  }
}

function sourceLabel(source: string) {
  switch (source) {
    case 'fhir': return 'EHR'
    case 'llm': return 'AI'
    case 'inferred': return 'Inferred'
    default: return 'Manual'
  }
}

function toggleEvidence(field: FormField) {
  if (expandedEvidence.value === field.fieldId) {
    expandedEvidence.value = null
  } else {
    expandedEvidence.value = field.fieldId
    if (field.evidence) {
      emit('viewSource', field.evidence, field.sourceNote)
    }
  }
}

function startEdit(field: FormField) {
  editValue.value = field.value
  editingField.value = field.fieldId
  if (field.evidence) {
    emit('viewSource', field.evidence, field.sourceNote)
  }
}

function startManualEntry(field: FormField) {
  manualEntryValue.value = ''
  manualEntryField.value = field.fieldId
}

function saveManualEntry(fieldId: string) {
  if (!manualEntryField.value) return  // Already closed (by Escape or another save)
  if (manualEntryValue.value.trim()) {
    formStore.editField(fieldId, manualEntryValue.value.trim())
    manualEntryField.value = null
    emit('fieldAccepted', fieldId)
  } else {
    manualEntryField.value = null
  }
}

function cancelManualEntry() {
  manualEntryField.value = null
}

function saveEdit(fieldId: string) {
  if (!editingField.value) return  // Already closed (by Escape or another save)
  formStore.editField(fieldId, editValue.value)
  editingField.value = null
  emit('fieldAccepted', fieldId)
}

function cancelEdit() {
  editingField.value = null  // Setting to null first means blur handler's saveEdit will no-op
}

// Expose scrollToField for bidirectional PDF ↔ ReviewQueue navigation
const flashFieldId = ref<string | null>(null)

function scrollToField(fieldId: string) {
  // First, expand the section containing this field
  const field = formStore.fields[fieldId]
  if (field) {
    sectionCollapsed.value[field.section] = false
  }

  // Wait for DOM update, then scroll and flash
  nextTick(() => {
    // Find the element by data attribute
    const el = document.querySelector(`[data-field-id="${fieldId}"]`) as HTMLElement
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      flashFieldId.value = fieldId
      setTimeout(() => { flashFieldId.value = null }, 2000)
    }
  })
}

defineExpose({ scrollToField })
</script>

<style scoped>
.review-root {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* --- Header --- */
.review-header {
  flex-shrink: 0;
  padding: 12px 16px 8px;
  border-bottom: 1px solid #E8EAED;
}
.progress-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.progress-label {
  font-size: 12px;
  font-weight: 600;
  color: #202124;
  white-space: nowrap;
}
.progress-track {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: #E8EAED;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: #34A853;
  border-radius: 3px;
  transition: width 0.4s ease;
}
.progress-stats {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}
.stat {
  font-size: 11px;
  font-weight: 500;
  padding: 1px 6px;
  border-radius: 8px;
}
.stat-accepted { color: #137333; background: #E6F4EA; }
.stat-edited { color: #1967D2; background: #E8F0FE; }
.stat-rejected { color: #C5221F; background: #FCE8E6; }

/* --- Section Toolbar --- */
.section-toolbar {
  flex-shrink: 0;
  display: flex;
  gap: 4px;
  padding: 4px 12px;
  border-bottom: 1px solid #F1F3F4;
}
.toolbar-btn {
  display: flex;
  align-items: center;
  font-size: 11px;
  font-weight: 500;
  color: #5F6368;
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  transition: all 0.1s;
}
.toolbar-btn:hover {
  color: #1967D2;
  background: #E8F0FE;
}

/* --- Scroll --- */
.review-scroll {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

/* --- Section Groups --- */
.section-group {
  border-bottom: 1px solid #F1F3F4;
}
.section-header {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 12px;
  cursor: pointer;
  background: #FAFAFA;
  user-select: none;
  transition: background 0.1s ease;
}
.section-header:hover { background: #F1F3F4; }
.section-title {
  font-size: 12px;
  font-weight: 600;
  color: #202124;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}
.section-count {
  font-size: 10px;
  color: #80868B;
  background: #E8EAED;
  padding: 0 5px;
  border-radius: 8px;
  margin-left: 2px;
}
.section-status {
  flex: 1;
  display: flex;
  justify-content: flex-end;
}
.status-badge {
  font-size: 10px;
  font-weight: 500;
  padding: 1px 6px;
  border-radius: 8px;
}
.status-pending { color: #B06000; background: #FEF7E0; }
.status-done { color: #137333; background: #E6F4EA; }
.status-rejected { color: #C5221F; background: #FCE8E6; }
.status-empty { color: #5F6368; background: #E8EAED; }
.save-section-btn {
  font-size: 10px;
  font-weight: 600;
  color: #137333;
  background: none;
  border: 1px solid #34A853;
  border-radius: 4px;
  padding: 1px 8px;
  cursor: pointer;
  margin-left: 6px;
  transition: all 0.15s ease;
}
.save-section-btn:hover { background: #E6F4EA; }
.ai-pending-badge {
  display: inline-flex;
  align-items: center;
  font-size: 9px;
  font-weight: 500;
  color: #7B1FA2;
  background: #F3E8FD;
  padding: 1px 6px;
  border-radius: 8px;
  margin-left: auto;
}

/* ========================================
   COMPACT TABLE VIEW (EHR sections)
   ======================================== */
.compact-table {
  padding: 4px 12px 6px;
}
.compact-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 8px;
  border-radius: 4px;
  min-height: 26px;
  transition: background 0.1s;
}
.compact-row:hover {
  background: #F1F3F4;
}
.compact-label {
  font-size: 11px;
  color: #80868B;
  min-width: 90px;
  flex-shrink: 0;
}
.compact-label-required {
  font-weight: 600;
  color: #3C4043;
}
.compact-value {
  font-size: 12px;
  color: #202124;
  font-weight: 500;
  flex: 1;
  word-break: break-word;
  white-space: normal;
}
.compact-empty {
  color: #BDC1C6;
  font-weight: 400;
}
.compact-clickable-value {
  cursor: pointer;
  transition: all 0.1s;
}
.compact-clickable-value:hover {
  text-decoration: underline dashed;
  color: #1967D2;
}
.compact-empty-required {
  cursor: text;
  font-weight: 400;
  font-size: 11px;
  color: #B06000;
  background: #FFF8E1;
  border: 1px dashed #F9AB00;
  border-radius: 4px;
  padding: 1px 8px;
  flex: 1;
  transition: all 0.1s;
}
.compact-empty-required:hover {
  background: #FFF3CD;
  border-color: #E69500;
}
.compact-clickable {
  cursor: text;
  border-bottom: 1px dashed #E0E0E0;
  transition: all 0.1s;
}
.compact-clickable:hover {
  color: #1967D2;
  border-bottom-color: #1967D2;
}
.compact-accepted { background: #F6FFF8; }
.compact-edited { background: #F0F7FF; }
.compact-rejected {
  opacity: 0.4;
  text-decoration: line-through;
}
.compact-edit-input {
  flex: 1;
  font-size: 12px;
  font-weight: 500;
  padding: 2px 6px;
  border: 1px solid #4285F4;
  border-radius: 4px;
  outline: none;
  background: #fff;
}
.compact-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 3px;
  cursor: pointer;
  flex-shrink: 0;
}
.compact-save {
  color: #fff;
  background: #34A853;
}
.compact-cancel {
  color: #5F6368;
  background: #E8EAED;
}

/* ========================================
   CARD VIEW (AI-extracted sections)
   ======================================== */
.section-fields {
  padding: 2px 8px 4px;
}
.review-item {
  border-radius: 6px;
  margin-bottom: 2px;
  transition: all 0.15s ease;
}

/* Compact row (accepted/rejected/edited) */
.item-compact {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  min-height: 28px;
}
.item-label-sm {
  font-size: 11px;
  color: #80868B;
  min-width: 80px;
  flex-shrink: 0;
}
.item-value-sm {
  font-size: 12px;
  color: #202124;
  font-weight: 500;
  flex: 1;
  word-break: break-word;
  white-space: normal;
}
.item-done { background: #FAFAFA; }
.item-done:hover { background: #F1F3F4; }
.item-rejected-row { opacity: 0.5; }
.item-edited-row { background: #F0F7FF; }
.undo-btn {
  font-size: 10px;
  color: #80868B;
  background: none;
  border: none;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
  text-decoration: underline;
}
.review-item:hover .undo-btn { opacity: 1; }

/* Pending card (needs review) */
.item-pending {
  padding: 10px 12px;
  background: #FAFBFC;
  border: 1px solid #E8EAED;
  border-left: 3px solid #F9AB00;
  border-radius: 6px;
  margin-bottom: 4px;
}
.item-top {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}
.item-label {
  font-size: 11px;
  font-weight: 500;
  color: #80868B;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}
.source-chip {
  font-size: 9px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 8px;
  text-transform: uppercase;
}
.item-top-link {
  margin-left: auto;
}
.source-fhir { color: #1967D2; background: #E8F0FE; }
.source-llm { color: #7B1FA2; background: #F3E8FD; }
.source-inferred { color: #E65100; background: #FFF3E0; }
.source-manual { color: #5F6368; background: #E8EAED; }
.item-value {
  font-size: 14px;
  font-weight: 500;
  color: #202124;
  line-height: 1.4;
  margin-bottom: 6px;
}

/* Evidence box */
.evidence-box {
  background: #F8F9FA;
  border: 1px solid #E8EAED;
  border-radius: 4px;
  padding: 6px 8px;
  margin-bottom: 6px;
}
.evidence-header {
  display: flex;
  align-items: center;
  font-size: 10px;
  font-weight: 600;
  color: #1967D2;
  text-transform: uppercase;
  margin-bottom: 4px;
}
.evidence-close {
  margin-left: auto;
  color: #80868B;
  background: none;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  transition: background 0.1s;
}
.evidence-close:hover { background: #E8EAED; color: #202124; }
.evidence-text {
  font-size: 12px;
  color: #3C4043;
  line-height: 1.5;
  font-style: italic;
  margin: 0;
}

/* Actions row */
.item-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.action-link {
  font-size: 11px;
  color: #1967D2;
  background: none;
  border: none;
  cursor: pointer;
  text-decoration: underline;
  padding: 0;
}
.action-link:hover { color: #1558B0; }
.action-buttons {
  display: flex;
  gap: 4px;
}
.btn-reject, .btn-edit, .btn-accept, .btn-save, .btn-cancel {
  font-size: 11px;
  font-weight: 600;
  padding: 4px 12px;
  border-radius: 4px;
  border: none;
  cursor: pointer;
  transition: all 0.1s ease;
}
.btn-reject { color: #C5221F; background: none; }
.btn-reject:hover { background: #FCE8E6; }
.btn-edit { color: #1967D2; background: none; }
.btn-edit:hover { background: #E8F0FE; }
.btn-accept { color: #fff; background: #34A853; }
.btn-accept:hover { background: #2D9249; }
.btn-save { color: #fff; background: #1967D2; }
.btn-cancel { color: #5F6368; background: #E8EAED; }

/* Edit row */
.edit-row {
  display: flex;
  gap: 4px;
  margin-top: 6px;
}
.edit-input {
  flex: 1;
  font-size: 13px;
  padding: 4px 8px;
  border: 1px solid #4285F4;
  border-radius: 4px;
  outline: none;
  background: #fff;
}

/* Empty row */
.item-empty-row { opacity: 0.6; }
.item-empty-row:hover { opacity: 1; }
.add-manually-btn {
  display: inline-flex;
  align-items: center;
  font-size: 10px;
  font-weight: 600;
  color: #1967D2;
  background: none;
  border: 1px solid #E8EAED;
  border-radius: 4px;
  padding: 1px 8px;
  cursor: pointer;
  margin-left: auto;
  transition: all 0.1s;
}
.add-manually-btn:hover { background: #E8F0FE; border-color: #1967D2; }
.manual-inline-input {
  flex: 1;
  min-width: 80px;
  font-size: 12px;
  padding: 2px 6px;
}
.btn-save-sm, .btn-cancel-sm {
  font-size: 10px;
  padding: 2px 8px;
}

/* ========================================
   GROUPED DRUG VIEW
   ======================================== */
.drug-section {
  padding: 4px 8px 6px;
}
.drug-group {
  border: 1px solid #F9AB00;
  border-left: 3px solid #F9AB00;
  border-radius: 6px;
  margin-bottom: 4px;
  background: #FFFBF0;
  transition: all 0.15s ease;
}
.drug-group-reviewed {
  border-color: #E8EAED;
  border-left-color: #34A853;
  background: #FAFAFA;
}
.drug-group-empty {
  border-color: #E8EAED;
  border-left-color: #E8EAED;
  background: #F8F9FA;
}
.drug-group-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
}
.drug-group-name {
  font-size: 13px;
  font-weight: 600;
  color: #202124;
  flex: 1;
  text-transform: capitalize;
}
.drug-group-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
.drug-btn {
  font-size: 10px !important;
  padding: 2px 10px !important;
}
.btn-remove {
  color: #80868B;
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 4px !important;
  border-radius: 4px;
  transition: all 0.1s;
}
.btn-remove:hover {
  color: #C5221F;
  background: #FCE8E6;
}
.drug-undo {
  opacity: 1 !important;
}
.drug-group-fields {
  padding: 0 10px 4px 30px;
}
.drug-field-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 1px 0;
  min-height: 22px;
}
.drug-field-label {
  font-size: 10px;
  color: #80868B;
  min-width: 70px;
  flex-shrink: 0;
}
.drug-field-value {
  font-size: 12px;
  color: #202124;
  font-weight: 500;
  flex: 1;
  word-break: break-word;
  white-space: normal;
}
.drug-group-footer {
  padding: 2px 10px 6px 30px;
  border-top: 1px solid rgba(0,0,0,0.05);
}
.drug-empty {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  font-size: 11px;
  color: #80868B;
}
.add-drug-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: calc(100% - 8px);
  margin: 4px 4px 8px;
  padding: 6px 12px;
  font-size: 11px;
  font-weight: 500;
  color: #1967D2;
  background: #E8F0FE;
  border: 1px dashed #4285F4;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}
.add-drug-btn:hover {
  background: #D2E3FC;
}

/* ========================================
   DRUG HISTORY TABLE VIEW
   ======================================== */
.drug-table-wrap {
  padding: 4px 8px 6px;
}
.drug-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.drug-table th {
  font-size: 10px;
  font-weight: 600;
  color: #80868B;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  text-align: left;
  padding: 4px 8px;
  border-bottom: 1px solid #E8EAED;
  white-space: nowrap;
}
.drug-table-row {
  transition: background 0.1s;
}
.drug-table-row:hover {
  background: #F1F3F4;
}
.drug-table-row-reviewed {
  opacity: 0.7;
}
.drug-table-cell {
  padding: 5px 8px;
  border-bottom: 1px solid #F1F3F4;
  vertical-align: top;
}
.drug-table-value {
  font-size: 12px;
  font-weight: 500;
  color: #202124;
  word-break: break-word;
  white-space: normal;
  display: block;
}
.drug-table-empty {
  color: #BDC1C6;
  font-weight: 400;
  cursor: text;
}
.drug-table-actions {
  padding: 4px 4px;
  border-bottom: 1px solid #F1F3F4;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 4px;
  justify-content: flex-end;
}
.drug-table-undo {
  opacity: 1 !important;
}
.drug-table-skeleton {
  padding: 8px 12px;
}

/* Skeleton */
.skeleton-line {
  height: 14px;
  background: #E8EAED;
  border-radius: 4px;
  margin: 6px 8px;
  animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
}

/* Locate in PDF button */
.locate-pdf-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  color: #80868B;
  background: none;
  border: none;
  border-radius: 3px;
  cursor: pointer;
  flex-shrink: 0;
  opacity: 0;
  transition: all 0.15s;
}
.compact-row:hover .locate-pdf-btn,
.review-item:hover .locate-pdf-btn,
.item-top .locate-pdf-btn,
.drug-table-row:hover .locate-pdf-btn {
  opacity: 1;
}
.locate-pdf-btn:hover {
  color: #1967D2;
  background: #E8F0FE;
}

/* Flash animation for scroll-to-field */
.field-flash {
  animation: field-flash-anim 0.5s ease-in-out 3;
}
@keyframes field-flash-anim {
  0%, 100% { background-color: transparent; }
  50% { background-color: #E8F0FE; }
}
</style>
