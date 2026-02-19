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
    "uhc": "https://www.bcbstx.com/star/pdf/nofr002.pdf",
    "bcbs": "https://www.bcbstx.com/star/pdf/nofr002.pdf",
}

# Mapping from frontend field_id to PDF form field name(s)
# Both UHC and BCBS naming conventions are included.
FIELD_ID_TO_PDF: dict[str, list[str]] = {
    "patient_name": ["Patient Name", "Patient's Name"],
    "patient_dob": ["Patient Date of Birth", "Patient's Date of Birth"],
    "patient_phone": ["Patient Phone Number", "Patient's Phone Number"],
    "patient_member_id": ["Member or Medicaid ID Number"],
    "provider_name": [
        "Requesting Provider or Facility Contact Name",
        "Prescriber's Office Contact Name",
    ],
    "provider_npi": [
        "Requesting Provider or Facility NPI Number",
        "Service Provider or Facility NPI Number",
        "Prescriber's NPI Number",
    ],
    "facility_name": [
        "Requesting Provider or Facility Name",
        "Service Provider or Facility Name",
    ],
    "condition_display": [
        "Planned Service or Procedure Diagnosis Description Row 1",
    ],
    "prescriber_name": [
        "Prescriber's Name",
    ],
    "prescriber_specialty": [
        "Requesting Provider or Facility Specialty",
        "Service Provider or Facility Specialty",
        "Prescriber's Specialty",
    ],
    "requested_drug": [
        "Planned Service or Procedure Row 1",
        "Requested Prescription Drug Name",
    ],
    "requested_dose": [
        "Requested Prescription Drug Strength",
    ],
    "insurer": ["Issuer Name", "Submitted to"],
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
        uhc_map = {
            "female": "Paitent Gender - Female",
            "male": "Paitent Gender - Male",
        }
        bcbs_map = {
            "female": "Patient's Gender - Female",
            "male": "Patient's Gender - Male",
            "other": "Patient's Gender - Other",
            "unknown": "Patient's Gender - Unknown",
        }
        for gmap in [uhc_map, bcbs_map]:
            fname = gmap.get(gender, "")
            if fname:
                _pdf_set(pdf, fname, "/Yes")

    # --- Address (needs parsing into components) ---
    if "patient_address" in accepted:
        addr_parts = [p.strip() for p in accepted["patient_address"].split(",")]
        filled += _pdf_set_many(pdf, {
            "Patient's Address - Street": addr_parts[0] if len(addr_parts) > 0 else "",
            "Patient's Address - City": addr_parts[1] if len(addr_parts) > 1 else "",
            "Patient's Address - State": addr_parts[2] if len(addr_parts) > 2 else "",
            "Patient's Address - ZIP Code": addr_parts[3] if len(addr_parts) > 3 else "",
        })

    # --- SNOMED → ICD-10 mapping ---
    if "condition_snomed" in accepted:
        icd_info = SNOMED_TO_ICD10.get(accepted["condition_snomed"])
        if icd_info:
            icd_code, icd_desc = icd_info
            filled += _pdf_set_many(pdf, {
                "Planned Service or Procedure Diagnosis Code Row 1": icd_code,
                "ICD Code": icd_code,
                "ICD Version": "ICD-10",
                "Diagnosis Description ICD Version Number": f"{icd_desc} (ICD-10: {icd_code})",
            })

    # --- Diagnosis combined with onset ---
    if "condition_display" in accepted:
        onset = accepted.get("condition_onset", patient_data.get("condition_onset", ""))
        diag_combined = (
            f"{accepted['condition_display']}, onset {onset}" if onset
            else accepted["condition_display"]
        )
        _pdf_set(pdf, "Patients diagnosis related to this request", diag_combined)

    # --- HCPCS code for requested drug ---
    if "requested_drug" in accepted:
        hcpcs = DRUG_TO_HCPCS.get(accepted["requested_drug"].lower(), "")
        if hcpcs:
            filled += _pdf_set_many(pdf, {
                "Planned Service or Procedure Code Row 1": hcpcs,
                "For Provider Administered Drugs Only - HCPCS Code": hcpcs,
            })
        _pdf_set(pdf, "New therapy", "/Yes")
        _pdf_set(pdf, "Planned Service or Procedure Start Date Row 1",
                 patient_data.get("latest_encounter_date", ""))

    # --- Prior drug history (step_therapy section) ---
    # Group prior drugs by their base key (e.g., prior_drug_methotrexate)
    prior_drugs: dict[str, dict[str, str]] = {}
    for field_id, value in accepted.items():
        if not field_id.startswith("prior_drug_"):
            continue
        # field_id format: prior_drug_{drugname}_{suffix}
        # suffix is one of: name, dates, reason, dose
        last_underscore = field_id.rfind("_")
        base = field_id[:last_underscore]
        suffix = field_id[last_underscore + 1:]
        if base not in prior_drugs:
            prior_drugs[base] = {}
        prior_drugs[base][suffix] = value

    drug_row = 1
    for drug_data in prior_drugs.values():
        if drug_row > 6:
            break
        drug_name = drug_data.get("name", "")
        if not drug_name:
            continue
        filled += _pdf_set_many(pdf, {
            f"Drugs Patient has Taken for Diagnosis - Drug Name {drug_row}": drug_name,
            f"Drugs Patient has Taken for Diagnosis - Drug Strength {drug_row}": drug_data.get("dose", ""),
            f"Drugs Patient has Taken for Diagnosis - Start Date {drug_row}": drug_data.get("dates", ""),
            f"Describe Response Reason for Failure or Allergy of Drug {drug_row}": drug_data.get("reason", ""),
            f"Strength of Drug {drug_row}": drug_data.get("dose", ""),
            f"Dates Started and Stopped or Approximate Duration of Drug {drug_row}": drug_data.get("dates", ""),
        })
        drug_row += 1

    # --- Administrative fields (always set) ---
    today = datetime.now().strftime("%m/%d/%Y")
    filled += _pdf_set_many(pdf, {
        "Submission Date": today,
        "Request Type - Initial": "/Yes",
        "Date Submitted": today,
    })

    # --- Section VI clinical justification ---
    if request.justification_text:
        # Use doctor-edited justification
        section_vi = request.justification_text
    else:
        # Auto-generate from extraction results
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
        _pdf_set(pdf, "SECTION VI  CLINICAL DOCUMENTATION SEE INSTRUCTIONS PAGE SECTION VI", section_vi)
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
