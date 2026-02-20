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
  sourceNote?: string
  section: string
  required?: boolean
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
  // Section VII - Diagnosis
  'condition_display',
  'icd10_code',
])

export const useFormStore = defineStore('form', () => {
  const fields = ref<Record<string, FormField>>({})
  const sectionViText = ref('')
  const justificationText = ref('')
  const justificationLoading = ref(false)
  const activeTab = ref('demographics')

  function _addField(
    fieldId: string,
    label: string,
    value: string,
    source: FormField['source'],
    section: string,
    criterionId?: string,
    evidence?: string,
    sourceNote?: string,
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
      sourceNote,
      required: MANDATORY_FIELDS.has(fieldId),
    }
  }

  function buildFromFhir(fhir: PatientFhir) {
    // --- Section I: Submission ---
    _addField('insurer', 'Insurer', fhir.insurer, 'fhir', 'demographics')
    const today = new Date().toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })
    _addField('submission_date', 'Submission Date', today, 'inferred', 'demographics')

    // --- Section III: Patient Information ---
    _addField('patient_name', 'Patient Name', fhir.name, 'fhir', 'demographics')
    _addField('patient_dob', 'Date of Birth', fhir.dob, 'fhir', 'demographics')
    _addField('patient_gender', 'Gender', fhir.gender, 'fhir', 'demographics')
    _addField('patient_phone', 'Phone Number', fhir.phone, 'fhir', 'demographics')
    _addField('patient_member_id', 'Member ID', fhir.member_id, 'fhir', 'demographics')
    _addField('patient_address', 'Address', fhir.address, 'fhir', 'demographics')

    // --- Section IV: Prescriber Information ---
    // FHIR encounter provider = the prescriber. LLM may refine in addExtractionResults().
    _addField('prescriber_name', 'Prescriber Name', fhir.provider_name, 'fhir', 'provider')
    _addField('prescriber_npi', 'Prescriber NPI', fhir.provider_npi, 'fhir', 'provider')

    // --- Section VII: Diagnosis ---
    _addField('condition_display', 'Diagnosis', fhir.condition_display, 'fhir', 'diagnosis')
    _addField('condition_onset', 'Onset Date', fhir.condition_onset, 'fhir', 'diagnosis')
    _addField('condition_snomed', 'SNOMED Code', fhir.condition_snomed, 'fhir', 'diagnosis')
    if (fhir.icd10_code) {
      _addField('icd10_code', 'ICD-10 Code', fhir.icd10_code, 'inferred', 'diagnosis')
    }
    if (fhir.icd_version) {
      _addField('icd_version', 'ICD Version', fhir.icd_version, 'inferred', 'diagnosis')
    }

    // --- Coverage (display only, not a PDF field) ---
    _addField('coverage_type', 'Coverage Type', fhir.coverage_type, 'fhir', 'demographics')

    // --- Place of Service (display only, informational) ---
    if (fhir.place_of_service) {
      _addField('place_of_service', 'Place of Service', fhir.place_of_service, 'fhir', 'demographics')
    }
  }

  function addExtractionResults(results: ExtractionResult[]) {
    for (const ext of results) {
      const cid = ext.criterion_id
      const sn = ext.source_note

      // Prescriber info — LLM refines the FHIR-populated prescriber fields.
      // Only overwrite if the field hasn't been reviewed yet by the doctor.
      if (ext.prescriber_name) {
        const existing = fields.value['prescriber_name']
        if (!existing || existing.status === 'suggested') {
          _addField('prescriber_name', 'Prescriber Name', ext.prescriber_name, 'llm', 'provider', cid, ext.evidence, sn)
        }
      }
      if (ext.prescriber_specialty) {
        _addField('prescriber_specialty', 'Prescriber Specialty', ext.prescriber_specialty, 'llm', 'provider', cid, ext.evidence, sn)
      }

      // Drug request (current therapy, not prior)
      if (ext.is_prior_therapy === false && ext.drug_name) {
        _addField('requested_drug', 'Requested Drug', ext.drug_name, 'llm', 'drug_request', cid, ext.evidence, sn)
        if (ext.drug_dose) {
          _addField('requested_dose', 'Drug Dose', ext.drug_dose, 'llm', 'drug_request', cid, ext.evidence, sn)
        }
        // Ensure companion fields exist so they appear in the same card
        const _ensureDrugField = (id: string, label: string) => {
          if (!fields.value[id]) _addField(id, label, '', 'manual', 'drug_request')
        }
        _ensureDrugField('hcpcs_code', 'HCPCS / J-Code')
        _ensureDrugField('quantity', 'Quantity')
        _ensureDrugField('days_supply', 'Days Supply')
        _ensureDrugField('route_of_admin', 'Route of Administration')
        _ensureDrugField('therapy_duration', 'Expected Therapy Duration')
      }

      // Prior therapy (drug history)
      if (ext.is_prior_therapy === true && ext.drug_name) {
        const rowKey = `prior_drug_${ext.drug_name.toLowerCase().replace(/\s+/g, '_')}`
        _addField(`${rowKey}_name`, `Prior Drug`, ext.drug_name, 'llm', 'step_therapy', cid, ext.evidence, sn)
        if (ext.drug_dates) {
          _addField(`${rowKey}_dates`, `Dates`, ext.drug_dates, 'llm', 'step_therapy', cid, ext.evidence, sn)
        }
        if (ext.failure_reason) {
          _addField(`${rowKey}_reason`, `Failure Reason`, ext.failure_reason, 'llm', 'step_therapy', cid, ext.evidence, sn)
        }
        if (ext.drug_dose) {
          _addField(`${rowKey}_dose`, `Dose`, ext.drug_dose, 'llm', 'step_therapy', cid, ext.evidence, sn)
        }
      }

      // Diagnosis evidence (from LLM) — informational, not a PDF field
      if (cid.includes('diagnosi') && ext.evidence && ext.evidence !== 'No mention found') {
        _addField('diagnosis_evidence', 'Diagnosis Evidence', ext.evidence, 'llm', 'diagnosis', cid, ext.evidence, sn)
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
      fields.value[fieldId].status = 'edited'
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

    // --- Section IV: Prescriber (extras not from FHIR) ---
    _maybe('prescriber_specialty', 'Prescriber Specialty', 'provider')
    _maybe('prescriber_address', 'Prescriber Address', 'provider')
    _maybe('prescriber_phone', 'Prescriber Phone', 'provider')
    _maybe('prescriber_fax', 'Prescriber Fax', 'provider')
    _maybe('prescriber_contact', 'Office Contact Name', 'provider')
    _maybe('prescriber_contact_phone', 'Office Contact Phone', 'provider')

    // --- Section V: Drug Request (extras) ---
    _maybe('hcpcs_code', 'HCPCS / J-Code', 'drug_request')
    _maybe('quantity', 'Quantity', 'drug_request')
    _maybe('days_supply', 'Days Supply', 'drug_request')
    _maybe('route_of_admin', 'Route of Administration', 'drug_request')
    _maybe('therapy_duration', 'Expected Therapy Duration', 'drug_request')

    // --- Section VII: Diagnosis (extras) ---
    _maybe('icd10_code', 'ICD-10 Code', 'diagnosis')
    _maybe('icd_version', 'ICD Version', 'diagnosis')
  }

  async function fetchJustification(uuid: string, metCriteria: ExtractionResult[] = []) {
    justificationLoading.value = true
    try {
      const res = await fetch(`/api/patients/${uuid}/justification`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ met_criteria: metCriteria }),
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

  function acceptAll() {
    for (const f of Object.values(fields.value)) {
      if (f.status === 'suggested' && f.value) {
        f.status = 'accepted'
      }
    }
  }

  const fieldsBySection = computed(() => {
    const sections: Record<string, FormField[]> = {}
    for (const f of Object.values(fields.value)) {
      if (!sections[f.section]) sections[f.section] = []
      sections[f.section].push(f)
    }
    // Sort mandatory fields to the top of each section
    for (const key of Object.keys(sections)) {
      sections[key].sort((a, b) => {
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

  const suggestedCount = computed(() =>
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

  function reset() {
    fields.value = {}
    sectionViText.value = ''
    justificationText.value = ''
    justificationLoading.value = false
    activeTab.value = 'demographics'
  }

  return {
    fields,
    sectionViText,
    justificationText,
    justificationLoading,
    activeTab,
    fieldsBySection,
    missingMandatoryFields,
    suggestedCount,
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
    acceptAll,
    reset,
  }
})
