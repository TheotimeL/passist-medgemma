import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { PatientFhir } from './patient'
import type { ExtractionResult } from './extraction'

export interface FormField {
  fieldId: string
  label: string
  value: string
  originalValue: string
  source: 'fhir' | 'llm' | 'inferred' | 'manual'
  status: 'suggested' | 'accepted' | 'rejected' | 'edited'
  criterionId?: string
  evidence?: string
  evidenceSnippets?: string[]
  sourceNote?: string
  section: string
  required?: boolean
  inferenceReason?: string
}

const MANDATORY_FIELDS = new Set([
  // Section I - Submission
  'insurer',
  // Section III - Patient Information
  'patient_name',
  'patient_dob',
  'patient_member_id',
  // Section IV - Prescriber Information
  'prescriber_name',
  'prescriber_npi',
  // Section V - Prescription Drug Information
  'requested_drug',
  'requested_dose',
  'quantity',
  'days_supply',
  'route_of_admin',
  // Section VII - Diagnosis
  'condition_display',
  'icd10_code',
])

export const useFormStore = defineStore('form', () => {
  const fields = ref<Record<string, FormField>>({})
  const justificationText = ref('')
  const justificationLoading = ref(false)
  const highlightedRequiredFields = ref<Set<string>>(new Set())

  function _addField(
    fieldId: string,
    label: string,
    value: string,
    source: FormField['source'],
    section: string,
    criterionId?: string,
    evidence?: string,
    sourceNote?: string,
    evidenceSnippets?: string[],
  ) {
    fields.value[fieldId] = {
      fieldId,
      label,
      value,
      originalValue: value,
      source,
      status: 'suggested',
      section,
      criterionId,
      evidence,
      evidenceSnippets,
      sourceNote,
      required: MANDATORY_FIELDS.has(fieldId),
    }
  }

  function buildFromFhir(fhir: PatientFhir, drugName?: string) {
    // --- Section I: Submission ---
    _addField('insurer', 'Insurer', fhir.insurer, 'fhir', 'demographics')
    const today = new Date().toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })
    _addField('submission_date', 'Submission Date', today, 'inferred', 'demographics')
    if (fields.value['submission_date']) fields.value['submission_date'].inferenceReason = "Today's date (auto-calculated)"

    // --- Section III: Patient Information ---
    _addField('patient_name', 'Patient Name', fhir.name, 'fhir', 'demographics')
    _addField('patient_dob', 'Date of Birth', fhir.dob, 'fhir', 'demographics')
    _addField('patient_gender', 'Gender', fhir.gender, 'fhir', 'demographics')
    _addField('patient_phone', 'Phone Number', fhir.phone, 'fhir', 'demographics')
    _addField('patient_member_id', 'Member ID', fhir.member_id, 'fhir', 'demographics')
    _addField('patient_address', 'Address', fhir.address, 'fhir', 'demographics')

    // --- Section IV: Prescriber Information ---
    // Prescriber fields are AI-only (populated via addExtractionResults or manual entry)

    // --- Section VII: Diagnosis ---
    _addField('condition_display', 'Diagnosis', fhir.condition_display, 'fhir', 'diagnosis')
    _addField('condition_onset', 'Onset Date', fhir.condition_onset, 'fhir', 'diagnosis')
    _addField('condition_snomed', 'SNOMED Code', fhir.condition_snomed, 'fhir', 'diagnosis')
    if (fhir.icd10_code) {
      _addField('icd10_code', 'ICD-10 Code', fhir.icd10_code, 'inferred', 'diagnosis')
      if (fields.value['icd10_code']) {
        fields.value['icd10_code'].inferenceReason = `Mapped from SNOMED code ${fhir.condition_snomed} via standard translation table`
      }
    }
    if (fhir.icd_version) {
      _addField('icd_version', 'ICD Version', fhir.icd_version, 'inferred', 'diagnosis')
      if (fields.value['icd_version']) {
        fields.value['icd_version'].inferenceReason = 'Inferred from ICD-10 code format'
      }
    }

    // --- Coverage (display only, not a PDF field) ---
    _addField('coverage_type', 'Coverage Type', fhir.coverage_type, 'fhir', 'demographics')

    // --- Place of Service (display only, informational) ---
    if (fhir.place_of_service) {
      _addField('place_of_service', 'Place of Service', fhir.place_of_service, 'fhir', 'demographics')
    }

    // --- Section V: Drug Request (always present so doctor can fill manually) ---
    _addField('requested_drug', 'Requested Drug', drugName || '', 'manual', 'drug_request')
    _addField('requested_dose', 'Strength', '', 'manual', 'drug_request')
  }

  function addExtractionResults(results: ExtractionResult[]) {
    // Helper: only overwrite a field if it hasn't been reviewed by the doctor
    function _addFieldIfUnreviewed(
      fieldId: string,
      label: string,
      value: string,
      source: FormField['source'],
      section: string,
      criterionId?: string,
      evidence?: string,
      sourceNote?: string,
      evidenceSnippets?: string[],
    ) {
      const existing = fields.value[fieldId]
      if (existing && existing.status !== 'suggested') return // Doctor already reviewed — don't overwrite
      _addField(fieldId, label, value, source, section, criterionId, evidence, sourceNote, evidenceSnippets)
    }

    for (const ext of results) {
      const cid = ext.criterion_id
      const sn = ext.source_note
      const snippets = ext.evidence_snippets

      // Prescriber info — LLM refines the FHIR-populated prescriber fields.
      // Only overwrite if the field hasn't been reviewed yet by the doctor.
      if (ext.prescriber_name) {
        _addFieldIfUnreviewed('prescriber_name', 'Prescriber Name', ext.prescriber_name, 'llm', 'provider', cid, ext.prescriber_name, sn, snippets)
      }
      if (ext.prescriber_specialty) {
        _addFieldIfUnreviewed('prescriber_specialty', 'Prescriber Specialty', ext.prescriber_specialty, 'llm', 'provider', cid, ext.prescriber_specialty, sn, snippets)
      }

      // Normalize is_prior_therapy (4B model may return string "true"/"false")
      const isPrior = ext.is_prior_therapy === true || (ext.is_prior_therapy as unknown) === 'true'
      const isCurrentDrug = ext.is_prior_therapy === false || (ext.is_prior_therapy as unknown) === 'false'

      // Drug request (current therapy, not prior)
      if (isCurrentDrug && ext.drug_name) {
        const drugEvidence = ext.drug_source_text || ext.evidence
        _addFieldIfUnreviewed('requested_drug', 'Requested Drug', ext.drug_name, 'llm', 'drug_request', cid, drugEvidence, sn, snippets)
        // Use drug_strength (from 4B parser) — don't fall back to compound drug_dose
        if (ext.drug_strength) {
          _addFieldIfUnreviewed('requested_dose', 'Strength', ext.drug_strength, 'llm', 'drug_request', cid, drugEvidence, sn, snippets)
        }
        // Ensure companion fields exist; use AI values when available
        const _ensureDrugField = (id: string, label: string, aiValue?: string) => {
          if (aiValue) {
            _addFieldIfUnreviewed(id, label, aiValue, 'llm', 'drug_request', cid, drugEvidence, sn, snippets)
          } else if (!fields.value[id]) {
            _addField(id, label, '', 'manual', 'drug_request')
          }
        }
        _ensureDrugField('quantity', 'Quantity', ext.drug_quantity || ext.drug_strength)
        _ensureDrugField('days_supply', 'Days Supply', ext.drug_days_supply)
        _ensureDrugField('route_of_admin', 'Route of Administration', ext.drug_route)
        _ensureDrugField('frequency', 'Frequency', ext.drug_frequency)
        _ensureDrugField('therapy_duration', 'Expected Therapy Duration')
      }

      // Prior therapy (drug history) — create all sub-fields including strength & frequency
      if (isPrior && ext.drug_name) {
        const rowKey = `prior_drug_${ext.drug_name.toLowerCase().replace(/\s+/g, '_')}`
        // Use 4B parser's source_text (exact drug snippet) for highlighting; fall back to full evidence
        const drugEvidence = ext.drug_source_text || ext.evidence
        const existingName = fields.value[`${rowKey}_name`]

        if (!existingName?.value) {
          // First time creating this row
          const priorStrength = ext.drug_strength || ''
          const priorFrequency = ext.drug_frequency || ''
          _addFieldIfUnreviewed(`${rowKey}_name`, `Prior Drug`, ext.drug_name, 'llm', 'step_therapy', cid, drugEvidence, sn, snippets)
          _addFieldIfUnreviewed(`${rowKey}_strength`, `Strength`, priorStrength, priorStrength ? 'llm' : 'manual', 'step_therapy', cid, drugEvidence, sn, snippets)
          _addFieldIfUnreviewed(`${rowKey}_frequency`, `Frequency`, priorFrequency, priorFrequency ? 'llm' : 'manual', 'step_therapy', cid, drugEvidence, sn, snippets)
          _addFieldIfUnreviewed(`${rowKey}_dates`, `Dates`, ext.drug_dates || '', ext.drug_dates ? 'llm' : 'manual', 'step_therapy', cid, drugEvidence, sn, snippets)
          _addFieldIfUnreviewed(`${rowKey}_reason`, `Failure Reason`, ext.failure_reason || '', ext.failure_reason ? 'llm' : 'manual', 'step_therapy', cid, drugEvidence, sn, snippets)
        } else if (existingName.status === 'suggested') {
          // Row exists — upgrade evidence if this criterion has a more precise drug_source_text
          if (ext.drug_source_text && existingName.evidence !== drugEvidence) {
            for (const f of Object.values(fields.value)) {
              if (f.fieldId.startsWith(rowKey) && f.status === 'suggested') {
                f.evidence = drugEvidence
                f.evidenceSnippets = snippets
                f.sourceNote = sn
              }
            }
          }
          const _fillIfEmpty = (id: string, label: string, value: string, source: FormField['source']) => {
            const existing = fields.value[id]
            if (existing && (existing.status !== 'suggested' || existing.value)) return
            _addField(id, label, value, source, 'step_therapy', cid, drugEvidence, sn, snippets)
          }
          if (ext.drug_strength) _fillIfEmpty(`${rowKey}_strength`, 'Strength', ext.drug_strength, 'llm')
          if (ext.drug_frequency) _fillIfEmpty(`${rowKey}_frequency`, 'Frequency', ext.drug_frequency, 'llm')
          if (ext.drug_dates) _fillIfEmpty(`${rowKey}_dates`, 'Dates', ext.drug_dates, 'llm')
          if (ext.failure_reason) _fillIfEmpty(`${rowKey}_reason`, 'Failure Reason', ext.failure_reason, 'llm')
        }
      }

      // Diagnosis evidence — attach AI evidence to the existing FHIR diagnosis field
      if (cid.includes('diagnosi') && ext.evidence && ext.evidence !== 'No mention found') {
        const existing = fields.value['condition_display']
        if (existing && existing.status === 'suggested') {
          existing.evidence = ext.evidence
          existing.evidenceSnippets = snippets
          existing.criterionId = cid
          existing.sourceNote = sn
        }
      }
    }
  }

  function acceptField(fieldId: string) {
    if (fields.value[fieldId]) {
      fields.value[fieldId].status = 'accepted'
    }
  }

  function rejectField(fieldId: string) {
    if (fields.value[fieldId]) {
      fields.value[fieldId].status = 'rejected'
      fields.value[fieldId].value = ''
    }
  }

  function editField(fieldId: string, newValue: string) {
    if (fields.value[fieldId]) {
      fields.value[fieldId].value = newValue
      fields.value[fieldId].status = 'accepted'
      highlightedRequiredFields.value.delete(fieldId)
    }
  }

  function undoField(fieldId: string) {
    if (fields.value[fieldId]) {
      fields.value[fieldId].status = 'suggested'
      fields.value[fieldId].value = fields.value[fieldId].originalValue
    }
  }

  function removeField(fieldId: string) {
    delete fields.value[fieldId]
  }

  function addManualDrug(
    prefix: string,
    section: string,
    fieldDefs: Array<{ suffix: string; label: string }>,
  ) {
    for (const def of fieldDefs) {
      _addField(`${prefix}${def.suffix}`, def.label, '', 'manual', section)
    }
  }

  function addManualFields() {
    // Only add if the field wasn't already populated by buildFromFhir / addExtractionResults.
    const _maybe = (id: string, label: string, section: string) => {
      if (!fields.value[id]) _addField(id, label, '', 'manual', section)
    }

    // --- Section I: Submission (extras) ---
    _maybe('group_number', 'Group Number', 'demographics')
    _maybe('insurer_phone', 'Insurer Phone', 'demographics')
    _maybe('insurer_fax', 'Insurer Fax', 'demographics')

    // --- Section IV: Prescriber ---
    // prescriber_name and prescriber_npi are NOT from FHIR — the FHIR provider
    // is the latest encounter GP, not the prescribing specialist.
    _maybe('prescriber_name', 'Prescriber Name', 'provider')
    _maybe('prescriber_npi', 'Prescriber NPI', 'provider')
    _maybe('prescriber_specialty', 'Prescriber Specialty', 'provider')
    _maybe('prescriber_address', 'Prescriber Address', 'provider')
    _maybe('prescriber_phone', 'Prescriber Phone', 'provider')
    _maybe('prescriber_fax', 'Prescriber Fax', 'provider')
    _maybe('prescriber_contact', 'Office Contact Name', 'provider')
    _maybe('prescriber_contact_phone', 'Office Contact Phone', 'provider')

    // --- Section V: Drug Request ---
    _maybe('requested_drug', 'Requested Drug', 'drug_request')
    _maybe('requested_dose', 'Strength', 'drug_request')
    _maybe('quantity', 'Quantity', 'drug_request')
    _maybe('days_supply', 'Days Supply', 'drug_request')
    _maybe('route_of_admin', 'Route of Administration', 'drug_request')
    _maybe('frequency', 'Frequency', 'drug_request')
    _maybe('therapy_duration', 'Expected Therapy Duration', 'drug_request')

    // --- Section VII: Diagnosis (extras) ---
    _maybe('icd10_code', 'ICD-10 Code', 'diagnosis')
    _maybe('icd_version', 'ICD Version', 'diagnosis')
  }

  async function fetchJustification(uuid: string, metCriteria: ExtractionResult[] = [], fieldOverrides: Record<string, string> = {}) {
    justificationLoading.value = true
    try {
      const res = await fetch(`/api/patients/${uuid}/justification`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ met_criteria: metCriteria, field_overrides: fieldOverrides }),
      })
      if (res.ok) {
        const data = await res.json()
        if (data.generated && data.text) {
          justificationText.value = data.text
        }
      }
    } catch {
      // Ignore — justification will be empty for doctor to write
    } finally {
      justificationLoading.value = false
    }
  }

  function updateJustificationCounts(metCount: number, totalCount: number, eligible?: boolean | null) {
    if (!justificationText.value) return
    const replacement = eligible === true
      ? 'all required policy criteria are satisfied'
      : `${metCount} of ${totalCount} policy criteria are satisfied`
    justificationText.value = justificationText.value.replace(
      /(?:\d+\s+of\s+\d+|all required)\s+policy criteria are satisfied/,
      replacement
    )
  }

  // Fields whose edits should propagate to the justification letter
  const JUSTIFICATION_FIELDS = ['icd10_code', 'patient_name', 'patient_dob', 'condition_display', 'condition_onset']

  const justificationOverrides = computed(() => {
    const overrides: Record<string, string> = {}
    for (const fid of JUSTIFICATION_FIELDS) {
      const f = fields.value[fid]
      if (f && f.value && f.value !== f.originalValue) {
        overrides[fid] = f.value
      }
    }
    return overrides
  })

  const fieldsBySection = computed(() => {
    const sections: Record<string, FormField[]> = {}
    for (const f of Object.values(fields.value)) {
      if (!sections[f.section]) sections[f.section] = []
      sections[f.section]!.push(f)
    }
    // Sort mandatory fields to the top of each section
    for (const key of Object.keys(sections)) {
      sections[key]!.sort((a, b) => {
        if (a.required && !b.required) return -1
        if (!a.required && b.required) return 1
        return 0
      })
    }
    return sections
  })

  const missingMandatoryFields = computed(() =>
    Object.values(fields.value).filter(
      f => f.required && (!f.value || f.status === 'rejected')
    )
  )

  const pendingReviewCount = computed(() =>
    Object.values(fields.value).filter(f => f.status === 'suggested' && f.value).length
  )

  const acceptedCount = computed(() =>
    Object.values(fields.value).filter(f => f.status === 'accepted').length
  )

  const totalFilledCount = computed(() =>
    Object.values(fields.value).filter(f => f.value).length
  )

  const fieldStatuses = computed(() => {
    const map: Record<string, string> = {}
    for (const f of Object.values(fields.value)) {
      map[f.fieldId] = f.value ? f.status : 'empty'
    }
    return map
  })

  function highlightEmptyRequired(section: string) {
    const sectionFields = Object.values(fields.value).filter(f => f.section === section)
    for (const f of sectionFields) {
      if (f.required && !f.value) {
        highlightedRequiredFields.value = new Set([...highlightedRequiredFields.value, f.fieldId])
      }
    }
  }

  function reset() {
    fields.value = {}
    justificationText.value = ''
    justificationLoading.value = false
    highlightedRequiredFields.value = new Set()
  }

  return {
    fields,
    justificationText,
    justificationLoading,
    fieldsBySection,
    missingMandatoryFields,
    acceptedCount,
    totalFilledCount,
    fieldStatuses,
    buildFromFhir,
    addManualDrug,
    addManualFields,
    addExtractionResults,
    fetchJustification,
    acceptField,
    rejectField,
    editField,
    undoField,
    removeField,
    pendingReviewCount,
    updateJustificationCounts,
    justificationOverrides,
    highlightedRequiredFields,
    highlightEmptyRequired,
    reset,
  }
})
