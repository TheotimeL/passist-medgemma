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
}

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
    }
  }

  function buildFromFhir(fhir: PatientFhir) {
    // Demographics
    _addField('patient_name', 'Patient Name', fhir.name, 'fhir', 'demographics')
    _addField('patient_dob', 'Date of Birth', fhir.dob, 'fhir', 'demographics')
    _addField('patient_gender', 'Gender', fhir.gender, 'fhir', 'demographics')
    _addField('patient_phone', 'Phone Number', fhir.phone, 'fhir', 'demographics')
    _addField('patient_member_id', 'Member ID', fhir.member_id, 'fhir', 'demographics')
    _addField('patient_address', 'Address', fhir.address, 'fhir', 'demographics')

    // Provider
    _addField('provider_name', 'Provider Name', fhir.provider_name, 'fhir', 'provider')
    _addField('provider_npi', 'Provider NPI', fhir.provider_npi, 'fhir', 'provider')
    _addField('facility_name', 'Facility Name', fhir.facility_name, 'fhir', 'provider')

    // Diagnosis
    _addField('condition_display', 'Diagnosis', fhir.condition_display, 'fhir', 'diagnosis')
    _addField('condition_onset', 'Onset Date', fhir.condition_onset, 'fhir', 'diagnosis')
    _addField('condition_snomed', 'SNOMED Code', fhir.condition_snomed, 'fhir', 'diagnosis')

    // Coverage
    _addField('insurer', 'Insurer', fhir.insurer, 'fhir', 'demographics')
    _addField('coverage_type', 'Coverage Type', fhir.coverage_type, 'fhir', 'demographics')
  }

  function addExtractionResults(results: ExtractionResult[]) {
    for (const ext of results) {
      const cid = ext.criterion_id
      const sn = ext.source_note

      // Prescriber info
      if (ext.prescriber_name) {
        _addField('prescriber_name', 'Prescriber Name', ext.prescriber_name, 'llm', 'provider', cid, ext.evidence, sn)
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

      // Diagnosis evidence (from LLM)
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
    // Demographics — additional fields from the PA form
    _addField('group_number', 'Group Number', '', 'manual', 'demographics')
    _addField('subscriber_name', 'Subscriber Name (if different)', '', 'manual', 'demographics')
    _addField('submission_date', 'Submission Date', '', 'manual', 'demographics')
    _addField('previous_auth_number', 'Previous Auth Number', '', 'manual', 'demographics')

    // Provider — additional provider/facility fields
    _addField('place_of_service', 'Place of Service', '', 'manual', 'provider')
    _addField('provider_phone', 'Provider Phone', '', 'manual', 'provider')
    _addField('provider_fax', 'Provider Fax', '', 'manual', 'provider')
    _addField('provider_contact', 'Contact Name', '', 'manual', 'provider')
    _addField('primary_care_provider', 'Primary Care Provider', '', 'manual', 'provider')
    _addField('primary_care_phone', 'Primary Care Phone', '', 'manual', 'provider')

    // Diagnosis — additional coding fields
    _addField('icd10_code', 'ICD-10 Code', '', 'manual', 'diagnosis')
    _addField('icd_version', 'ICD Version', '', 'manual', 'diagnosis')

    // Drug Request — additional service/drug details
    _addField('drug_strength', 'Drug Strength', '', 'manual', 'drug_request')
    _addField('hcpcs_code', 'HCPCS / J-Code', '', 'manual', 'drug_request')
    _addField('service_start_date', 'Service Start Date', '', 'manual', 'drug_request')
    _addField('service_end_date', 'Service End Date', '', 'manual', 'drug_request')
    _addField('quantity', 'Quantity / Units', '', 'manual', 'drug_request')
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
    return sections
  })

  const suggestedCount = computed(() =>
    Object.values(fields.value).filter(f => f.status === 'suggested' && f.value).length
  )

  const acceptedCount = computed(() =>
    Object.values(fields.value).filter(f => f.status === 'accepted').length
  )

  const totalFilledCount = computed(() =>
    Object.values(fields.value).filter(f => f.value).length
  )

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
    suggestedCount,
    acceptedCount,
    totalFilledCount,
    buildFromFhir,
    addManualDrug,
    addManualFields,
    addExtractionResults,
    fetchJustification,
    acceptField,
    rejectField,
    editField,
    undoField,
    acceptAll,
    reset,
  }
})
