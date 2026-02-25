"""Pydantic models for API request/response types."""

from __future__ import annotations

from pydantic import BaseModel


class PatientSummary(BaseModel):
    uuid: str
    name: str
    files: list[str]
    has_fhir: bool
    eligible: bool | None = None
    eligibility_reason: str = ""
    policy: str = ""


class PatientFhir(BaseModel):
    name: str
    dob: str
    gender: str
    phone: str
    member_id: str
    address: str
    provider_name: str
    provider_npi: str
    facility_name: str
    condition_snomed: str
    condition_display: str
    condition_onset: str
    medications: list[dict]
    coverage_type: str
    insurer: str
    latest_encounter_date: str
    encounter_class: str
    icd10_code: str = ""
    icd_version: str = ""
    place_of_service: str = ""


class NoteFile(BaseModel):
    filename: str
    content: str
    type: str = "soap"
    date: str = ""
    title: str = ""


class NotesResponse(BaseModel):
    files: list[NoteFile]


class ExtractionResult(BaseModel):
    criterion_id: str
    met: bool
    evidence: str
    drug_name: str | None = None
    drug_dose: str | None = None
    drug_dates: str | None = None
    is_prior_therapy: bool | None = None
    failure_reason: str | None = None
    prescriber_name: str | None = None
    prescriber_specialty: str | None = None
    source_note: str | None = None
    source_uri: str | None = None


class PolicyStatusResponse(BaseModel):
    overall: bool | None
    met_count: int
    total_count: int
    pending_count: int
    criteria: list[dict]


class FormFieldValue(BaseModel):
    field_id: str
    value: str
    source: str  # "fhir" | "llm" | "inferred" | "manual"
    criterion_id: str | None = None
    evidence: str | None = None


class FormFieldUpdate(BaseModel):
    field_id: str
    value: str


class GeneratePdfRequest(BaseModel):
    uuid: str
    fields: list[FormFieldUpdate]
    insurer: str = "bcbs"
    justification_text: str | None = None  # Doctor-edited justification (overrides auto-generated)


class GeneratePasBundleRequest(BaseModel):
    uuid: str
    fields: list[FormFieldUpdate]
    insurer: str = "bcbs"
    justification_text: str | None = None
    met_criteria: list[dict] = []
    overrides: dict[str, bool] = {}
