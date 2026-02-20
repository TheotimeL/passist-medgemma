"""Form data mapping and PDF generation endpoints."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from fastapi.responses import FileResponse

from api.schemas import GeneratePdfRequest
from api.patients import _find_fhir_bundle
from patient_data import (
    load_patient_from_fhir,
    format_section_vi,
    SNOMED_TO_ICD10,
    DRUG_TO_HCPCS,
)
from policy_tree import load_tree, get_status, get_all_criteria, CriterionResult, tree_to_dict
from pdf_form import PDFFormManager

router = APIRouter()

TREE_PATH = "rheumatoid_arthritis_initial_auth_decision_tree_enriched.json"

# Form template URLs
FORM_TEMPLATES = {
    "bcbs": "https://www.bcbstx.com/star/pdf/nofr002.pdf",
}

# ---------------------------------------------------------------------------
# Field mapping categories
# ---------------------------------------------------------------------------
# Every frontend field_id falls into exactly ONE category:
#   1. FIELD_ID_TO_PDF  — simple text fields (field_id → PDF field names)
#   2. CHECKBOX_FIELDS  — handled via gender logic in generate_pdf()
#   3. Computed          — special logic in generate_pdf() (address, diagnosis combo,
#                         prior drug rows, manual drug rows, SNOMED→ICD, HCPCS, etc.)
#   4. DISPLAY_ONLY     — informational UI fields, never written to PDF

# Verified against the actual BCBS TX form (nofr002.pdf, 143 fields).
FIELD_ID_TO_PDF: dict[str, list[str]] = {
    # --- Section I: Submission ---
    "insurer": ["Submitted to"],
    "insurer_phone": ["Submitted to Phone Number"],
    "insurer_fax": ["Submitted to Fax Number"],
    "submission_date": ["Date Submitted"],

    # --- Section III: Patient Information ---
    "patient_name": ["Patient's Name"],
    "patient_dob": ["Patient's Date of Birth"],
    "patient_phone": ["Patient's Phone Number"],
    "patient_member_id": ["Member or Medicaid ID Number"],
    "group_number": ["Group Number"],
    # patient_gender → checkbox (computed)
    # patient_address → parsed into Street/City/State/ZIP (computed)

    # --- Section IV: Prescriber Information ---
    "prescriber_name": ["Prescriber's Name"],
    "prescriber_npi": ["Prescriber's NPI Number"],
    "prescriber_specialty": ["Prescriber's Specialty"],
    "prescriber_phone": ["Prescriber's Phone Number"],
    "prescriber_fax": ["Prescriber's Fax Number"],
    "prescriber_contact": ["Prescriber's Office Contact Name"],
    "prescriber_contact_phone": ["Prescriber's Office Contact Phone Number"],

    # --- Section V: Prescription Drug Request ---
    "requested_drug": ["Requested Prescription Drug Name"],
    "requested_dose": ["Requested Prescription Drug Strength"],
    "quantity": ["Requested Prescription Drug Quantity"],
    "days_supply": ["Requested Prescription Drug Days Supply"],
    "route_of_admin": ["Requested Prescription Drug Route of Administration"],
    "therapy_duration": ["Requested Prescription Drug Expected Therapy Duration"],
    "hcpcs_code": ["For Provider Administered Drugs Only - HCPCS Code"],

    # --- Section VII: Diagnosis ---
    "icd10_code": ["ICD Code"],
    "icd_version": ["ICD Version"],
}

# Frontend fields that exist for display/info only — never written to PDF.
DISPLAY_ONLY_FIELDS: set[str] = {
    "coverage_type",
    "diagnosis_evidence",
    "condition_snomed",  # used for auto-mapping to ICD, not a PDF field itself
    "condition_onset",   # combined with condition_display into diagnosis (computed)
    "place_of_service",  # informational (FHIR encounter class)
}

# Computed field_ids — handled by special-case logic in generate_pdf().
# Listed here for documentation / validation only.
_COMPUTED_FIELDS: set[str] = {
    "patient_gender",      # checkbox
    "patient_address",     # parsed into 4 address fields
    "prescriber_address",  # parsed into 4 address fields
    "condition_display",   # combined with onset → "Patients diagnosis related..."
    # prior_drug_* and manual_prior_drug_* → drug history rows 1–6
    # manual_drug_* → requested drug fields (last one wins)
}

# Computed fields that map to multiple PDF form fields (checkboxes, split address, etc.)
# Merged into the field-mapping endpoint so PdfViewer can highlight them.
COMPUTED_FIELD_TO_PDF: dict[str, list[str]] = {
    "patient_gender": [
        "Patient's Gender - Male", "Patient's Gender - Female",
        "Patient's Gender - Other", "Patient's Gender - Unknown",
    ],
    "patient_address": [
        "Patient's Address - Street", "Patient's Address - City",
        "Patient's Address - State", "Patient's Address - ZIP Code",
    ],
    "prescriber_address": [
        "Prescriber's Address - Street", "Prescriber's Address - City",
        "Prescriber's Address - State", "Prescriber's Address - ZIP Code",
    ],
    "condition_display": ["Patients diagnosis related to this request"],
}


def _pdf_set(pdf: PDFFormManager, field_name: str, value: str) -> bool:
    """Set a single PDF field if it exists. Returns True if set."""
    if value and field_name in pdf.fields_map:
        pdf.fields_map[field_name]["value"] = value
        return True
    return False


def _pdf_set_many(pdf: PDFFormManager, fields: dict[str, str]) -> int:
    """Set multiple PDF fields. Returns count of fields set."""
    return sum(_pdf_set(pdf, name, val) for name, val in fields.items())


@router.get("/form/field-mapping")
def get_field_mapping():
    """Return the frontend fieldId → PDF field name mapping (including computed fields)."""
    return {**FIELD_ID_TO_PDF, **COMPUTED_FIELD_TO_PDF}


@router.get("/policy/tree")
def get_policy_tree():
    """Return the enriched policy decision tree."""
    tree = load_tree(TREE_PATH)
    return tree_to_dict(tree)


@router.get("/policy/criteria")
def get_policy_criteria():
    """Return flat list of all leaf criteria."""
    tree = load_tree(TREE_PATH)
    criteria = get_all_criteria(tree)
    return [
        {
            "id": cid,
            "name": leaf.name or leaf.summary,
            "source_text": leaf.source_text,
            "search_description": leaf.search_description,
            "keywords": leaf.keywords,
            "negated": leaf.negated,
        }
        for cid, leaf in criteria.items()
    ]


class JustificationRequest(BaseModel):
    met_criteria: list[dict] = []


@router.post("/patients/{uuid}/justification")
def get_justification(uuid: str, body: JustificationRequest):
    """Generate the clinical justification letter for a patient."""
    bundle_path = _find_fhir_bundle(uuid)
    if not bundle_path:
        raise HTTPException(status_code=404, detail="No FHIR bundle found")

    patient_data = load_patient_from_fhir(bundle_path)
    tree = load_tree(TREE_PATH)

    extraction_results: list[dict] = body.met_criteria

    # Fall back to disk-cached results if frontend didn't send any
    if not extraction_results:
        for json_file in Path(".").glob("extraction_*.json"):
            try:
                data = json.loads(json_file.read_text())
                if isinstance(data, list):
                    for entry in data:
                        if entry.get("uuid", "").startswith(uuid[:8]):
                            extraction_results = entry.get("met_criteria", [])
                            break
            except (json.JSONDecodeError, KeyError):
                continue

    if not extraction_results:
        return {"text": "", "generated": False}

    # Build policy status
    cr_results: dict[str, CriterionResult] = {}
    for ext in extraction_results:
        cid = ext.get("criterion_id", "")
        cr_results[cid] = CriterionResult(
            criterion_id=cid,
            met=ext.get("met", False),
            evidence=ext.get("evidence", ""),
        )
    policy_status = get_status(tree, cr_results)
    section_vi = format_section_vi(patient_data, policy_status, extraction_results, tree)
    return {"text": section_vi, "generated": True}


@router.post("/form/generate-pdf")
def generate_pdf(request: GeneratePdfRequest):
    """Generate a filled PA form PDF.

    ONLY populates fields that the user explicitly accepted/edited
    in the frontend review queue. Unaccepted suggestions are omitted.
    """
    bundle_path = _find_fhir_bundle(request.uuid)
    if not bundle_path:
        raise HTTPException(status_code=404, detail="No FHIR bundle found")

    patient_data = load_patient_from_fhir(bundle_path)
    tree = load_tree(TREE_PATH)

    template_url = FORM_TEMPLATES.get(request.insurer)
    if not template_url:
        raise HTTPException(status_code=400, detail=f"Unknown insurer: {request.insurer}")

    pdf = PDFFormManager(template_url)
    accepted = {f.field_id: f.value for f in request.fields}
    filled = 0


# --- Apply simple field mappings ---
    for field_id, value in accepted.items():
        pdf_names = FIELD_ID_TO_PDF.get(field_id, [])
        for name in pdf_names:
            if _pdf_set(pdf, name, value):
                filled += 1

    # --- Gender checkbox ---
    if "patient_gender" in accepted:
        gender = accepted["patient_gender"].lower()
        gender_map = {
            "female": "Patient's Gender - Female",
            "male": "Patient's Gender - Male",
            "other": "Patient's Gender - Other",
            "unknown": "Patient's Gender - Unknown",
        }
        fname = gender_map.get(gender, "")
        if fname:
            _pdf_set(pdf, fname, "/On")

    # --- Prescriber Address (parsed into BCBS address components) ---
    if "prescriber_address" in accepted:
        addr_parts = [p.strip() for p in accepted["prescriber_address"].split(",")]
        filled += _pdf_set_many(pdf, {
            "Prescriber's Address - Street": addr_parts[0] if len(addr_parts) > 0 else "",
            "Prescriber's Address - City": addr_parts[1] if len(addr_parts) > 1 else "",
            "Prescriber's Address - State": addr_parts[2] if len(addr_parts) > 2 else "",
            "Prescriber's Address - ZIP Code": addr_parts[3] if len(addr_parts) > 3 else "",
        })

    # --- Address (parsed into BCBS address components) ---
    if "patient_address" in accepted:
        addr_parts = [p.strip() for p in accepted["patient_address"].split(",")]
        filled += _pdf_set_many(pdf, {
            "Patient's Address - Street": addr_parts[0] if len(addr_parts) > 0 else "",
            "Patient's Address - City": addr_parts[1] if len(addr_parts) > 1 else "",
            "Patient's Address - State": addr_parts[2] if len(addr_parts) > 2 else "",
            "Patient's Address - ZIP Code": addr_parts[3] if len(addr_parts) > 3 else "",
        })

    # --- SNOMED → ICD-10 auto-mapping (only if doctor didn't set ICD manually) ---
    if "condition_snomed" in accepted and "icd10_code" not in accepted:
        icd_info = SNOMED_TO_ICD10.get(accepted["condition_snomed"])
        if icd_info:
            icd_code, _ = icd_info
            filled += _pdf_set_many(pdf, {
                "ICD Code": icd_code,
                "ICD Version": "ICD-10",
            })

    # --- Diagnosis combined with onset ---
    if "condition_display" in accepted:
        onset = accepted.get("condition_onset", patient_data.get("condition_onset", ""))
        diag_combined = (
            f"{accepted['condition_display']}, onset {onset}" if onset
            else accepted["condition_display"]
        )
        _pdf_set(pdf, "Patients diagnosis related to this request", diag_combined)

    # --- HCPCS auto-inference from drug name ---
    if "requested_drug" in accepted:
        if "hcpcs_code" not in accepted:
            hcpcs = DRUG_TO_HCPCS.get(accepted["requested_drug"].lower(), "")
            if hcpcs:
                _pdf_set(pdf, "For Provider Administered Drugs Only - HCPCS Code", hcpcs)
                filled += 1
        _pdf_set(pdf, "New therapy", "/On")

    # --- Manual drug request (manual_drug_N_name/dose → requested drug fields) ---
    # If the doctor manually added a requested drug, it overrides the AI-extracted one.
    # Only the LAST manual drug wins (can't have multiple requested drugs in one PDF).
    manual_drug_entries: list[tuple[str, str]] = []  # (name, dose)
    manual_drug_groups: dict[str, dict[str, str]] = {}
    for field_id, value in accepted.items():
        if not field_id.startswith("manual_drug_"):
            continue
        # Parse: manual_drug_N_suffix (e.g., manual_drug_1_name, manual_drug_1_dose)
        # Find the suffix after the last underscore that is a known suffix
        for suffix in ("_name", "_dose"):
            if field_id.endswith(suffix):
                base = field_id[: -len(suffix)]
                if base not in manual_drug_groups:
                    manual_drug_groups[base] = {}
                manual_drug_groups[base][suffix.lstrip("_")] = value
                break

    for group_data in manual_drug_groups.values():
        name = group_data.get("name", "")
        dose = group_data.get("dose", "")
        if name:
            manual_drug_entries.append((name, dose))

    if manual_drug_entries:
        # Last entry wins
        last_name, last_dose = manual_drug_entries[-1]
        _pdf_set(pdf, "Requested Prescription Drug Name", last_name)
        if last_dose:
            _pdf_set(pdf, "Requested Prescription Drug Strength", last_dose)

    # --- Prior drug history (rows 1–6) ---
    # Group prior drugs by base key (e.g., prior_drug_methotrexate, manual_prior_drug_1).
    # Uses known suffixes (_name, _dates, _reason, _dose) instead of fragile rfind("_").
    _PRIOR_DRUG_SUFFIXES = ("_name", "_dates", "_reason", "_dose")
    prior_drugs: dict[str, dict[str, str]] = {}
    for field_id, value in accepted.items():
        if not (field_id.startswith("prior_drug_") or field_id.startswith("manual_prior_drug_")):
            continue
        for suffix in _PRIOR_DRUG_SUFFIXES:
            if field_id.endswith(suffix):
                base = field_id[: -len(suffix)]
                if base not in prior_drugs:
                    prior_drugs[base] = {}
                prior_drugs[base][suffix.lstrip("_")] = value
                break

    drug_row = 1
    for drug_data in prior_drugs.values():
        if drug_row > 6:
            break
        drug_name = drug_data.get("name", "")
        if not drug_name:
            continue
        filled += _pdf_set_many(pdf, {
            f"Drugs Patient has Taken for Diagnosis - Drug Name {drug_row}": drug_name,
            f"Strength of Drug {drug_row}": drug_data.get("dose", ""),
            f"Dates Started and Stopped or Approximate Duration of Drug {drug_row}": drug_data.get("dates", ""),
            f"Describe Response Reason for Failure or Allergy of Drug {drug_row}": drug_data.get("reason", ""),
        })
        drug_row += 1

    # --- Administrative (always set if not already provided) ---
    if "submission_date" not in accepted:
        today = datetime.now().strftime("%m/%d/%Y")
        _pdf_set(pdf, "Date Submitted", today)

    # --- Section IX clinical justification ---
    if request.justification_text:
        section_vi = request.justification_text
    else:
        section_vi = ""
        extraction_results: list[dict] = []
        for json_file in Path(".").glob("extraction_*.json"):
            try:
                data = json.loads(json_file.read_text())
                if isinstance(data, list):
                    for entry in data:
                        if entry.get("uuid", "").startswith(request.uuid[:8]):
                            extraction_results = entry.get("met_criteria", [])
                            break
            except (json.JSONDecodeError, KeyError):
                continue
        if extraction_results:
            cr_results: dict[str, CriterionResult] = {}
            for ext in extraction_results:
                cid = ext.get("criterion_id", "")
                cr_results[cid] = CriterionResult(
                    criterion_id=cid,
                    met=ext.get("met", False),
                    evidence=ext.get("evidence", ""),
                )
            policy_status = get_status(tree, cr_results)
            section_vi = format_section_vi(patient_data, policy_status, extraction_results, tree)

    if section_vi:
        _pdf_set(pdf, "Section IX \u2015 Justification (See Instruction Page Section IX)", section_vi)

    # --- Generate PDF ---
    output_path = tempfile.mktemp(suffix=".pdf", prefix="pa_form_")
    pdf.generate_pdf(output_path)
    print(f"[PDF] Generated with {filled} accepted fields")

    return FileResponse(
        output_path,
        media_type="application/pdf",
        filename=f"pa_form_{patient_data['name'].replace(' ', '_')}.pdf",
    )


def validate_field_coverage(field_ids: list[str]) -> dict[str, list[str]]:
    """Check which frontend field_ids are mapped, display-only, computed, or orphaned.

    Dev-only utility. Call from a test or debug endpoint to verify no fields are
    silently lost.

    Returns dict with keys: mapped, display_only, computed, dynamic, orphaned.
    """
    result: dict[str, list[str]] = {
        "mapped": [],
        "display_only": [],
        "computed": [],
        "dynamic": [],
        "orphaned": [],
    }
    for fid in field_ids:
        if fid in FIELD_ID_TO_PDF:
            result["mapped"].append(fid)
        elif fid in DISPLAY_ONLY_FIELDS:
            result["display_only"].append(fid)
        elif fid in _COMPUTED_FIELDS:
            result["computed"].append(fid)
        elif (
            fid.startswith("prior_drug_")
            or fid.startswith("manual_prior_drug_")
            or fid.startswith("manual_drug_")
        ):
            result["dynamic"].append(fid)
        else:
            result["orphaned"].append(fid)
    return result
