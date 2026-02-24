import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface PatientSummary {
  uuid: string
  name: string
  files: string[]
  has_fhir: boolean
  eligible: boolean | null
  eligibility_reason: string
  policy: string
}

export interface PatientFhir {
  name: string
  dob: string
  gender: string
  phone: string
  member_id: string
  address: string
  provider_name: string
  provider_npi: string
  facility_name: string
  condition_snomed: string
  condition_display: string
  condition_onset: string
  medications: Array<{ name: string; rxnorm: string; status: string }>
  coverage_type: string
  insurer: string
  latest_encounter_date: string
  encounter_class: string
  icd10_code: string
  icd_version: string
  place_of_service: string
}

export interface NoteFile {
  filename: string
  content: string
  type: string
  date: string
  title: string
}

export const usePatientStore = defineStore('patient', () => {
  const patients = ref<PatientSummary[]>([])
  const selectedUuid = ref<string | null>(null)
  const fhirData = ref<PatientFhir | null>(null)
  const notes = ref<NoteFile[]>([])
  const selectedNoteIndex = ref(0)
  const loadingPatients = ref(false)
  const loadingFhir = ref(false)
  const loadingNotes = ref(false)

  async function fetchPatients(policy?: string) {
    loadingPatients.value = true
    try {
      const url = policy ? `/api/patients?policy=${encodeURIComponent(policy)}` : '/api/patients'
      const res = await fetch(url)
      patients.value = await res.json()
    } finally {
      loadingPatients.value = false
    }
  }

  async function fetchFhir(uuid: string) {
    loadingFhir.value = true
    fhirData.value = null
    try {
      const res = await fetch(`/api/patients/${uuid}/fhir`)
      if (res.ok) {
        fhirData.value = await res.json()
      }
    } finally {
      loadingFhir.value = false
    }
  }

  async function fetchNotes(uuid: string) {
    loadingNotes.value = true
    notes.value = []
    selectedNoteIndex.value = 0
    try {
      const res = await fetch(`/api/patients/${uuid}/notes`)
      if (res.ok) {
        const data = await res.json()
        notes.value = data.files
      }
    } finally {
      loadingNotes.value = false
    }
  }

  function selectNoteByFilename(filename: string) {
    const idx = notes.value.findIndex(n => n.filename === filename)
    if (idx !== -1) {
      selectedNoteIndex.value = idx
    }
  }

  async function selectPatient(uuid: string) {
    selectedUuid.value = uuid
    await Promise.all([fetchFhir(uuid), fetchNotes(uuid)])
  }

  return {
    patients,
    selectedUuid,
    fhirData,
    notes,
    selectedNoteIndex,
    loadingPatients,
    loadingFhir,
    loadingNotes,
    fetchPatients,
    fetchFhir,
    fetchNotes,
    selectPatient,
    selectNoteByFilename,
  }
})
