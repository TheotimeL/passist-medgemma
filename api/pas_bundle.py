"""Da Vinci PAS FHIR Bundle generation endpoint.

Constructs a FHIR Bundle (type: collection) packaging patient demographics,
diagnosis, requested drug, prior therapy history, and AI-extracted clinical
evidence into a standards-compliant Da Vinci PAS Request Bundle.
"""

from __future__ import annotations

import logging
import uuid as uuid_mod
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from api.schemas import GeneratePasBundleRequest
from api.patients import _find_fhir_bundle
from patient_data import load_patient_from_fhir, SNOMED_TO_ICD10, DRUG_TO_HCPCS
from config import TREE_PATH
from policy_tree import load_tree, get_all_criteria

logger = logging.getLogger(__name__)
router = APIRouter()


def _urn() -> str:
    return f"urn:uuid:{uuid_mod.uuid4()}"


def _make_patient_resource(patient_data: dict) -> tuple[str, dict]:
    """Build a FHIR Patient resource from parsed FHIR data."""
    full_url = _urn()
    name_parts = patient_data["name"].split()
    given = name_parts[:-1] if len(name_parts) > 1 else name_parts
    family = name_parts[-1] if len(name_parts) > 1 else ""

    resource: dict = {
        "resourceType": "Patient",
        "name": [{"use": "official", "family": family, "given": given}],
        "birthDate": patient_data.get("dob", ""),
        "gender": patient_data.get("gender", ""),
    }

    if patient_data.get("member_id"):
        resource["identifier"] = [{
            "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MB"}]},
            "value": patient_data["member_id"],
        }]

    if patient_data.get("address"):
        addr_parts = [p.strip() for p in patient_data["address"].split(",")]
        addr: dict = {}
        if len(addr_parts) > 0:
            addr["line"] = [addr_parts[0]]
        if len(addr_parts) > 1:
            addr["city"] = addr_parts[1]
        if len(addr_parts) > 2:
            addr["state"] = addr_parts[2]
        if len(addr_parts) > 3:
            addr["postalCode"] = addr_parts[3]
        resource["address"] = [addr]

    if patient_data.get("phone"):
        resource["telecom"] = [{"system": "phone", "value": patient_data["phone"]}]

    return full_url, resource


def _make_practitioner_resource(patient_data: dict) -> tuple[str, dict]:
    """Build a FHIR Practitioner resource."""
    full_url = _urn()
    name_parts = patient_data.get("provider_name", "").split()
    given = name_parts[:-1] if len(name_parts) > 1 else name_parts
    family = name_parts[-1] if len(name_parts) > 1 else ""

    resource: dict = {
        "resourceType": "Practitioner",
        "name": [{"family": family, "given": given}],
    }
    if patient_data.get("provider_npi"):
        resource["identifier"] = [{
            "system": "http://hl7.org/fhir/sid/us-npi",
            "value": patient_data["provider_npi"],
        }]
    return full_url, resource


def _make_organization_resource(patient_data: dict) -> tuple[str, dict]:
    """Build a FHIR Organization resource."""
    full_url = _urn()
    resource = {
        "resourceType": "Organization",
        "name": patient_data.get("facility_name", ""),
    }
    return full_url, resource


def _make_coverage_resource(patient_data: dict, patient_url: str) -> tuple[str, dict]:
    """Build a FHIR Coverage resource."""
    full_url = _urn()
    resource: dict = {
        "resourceType": "Coverage",
        "status": "active",
        "beneficiary": {"reference": patient_url},
        "payor": [{"display": patient_data.get("insurer", "")}],
    }
    if patient_data.get("coverage_type"):
        resource["type"] = {
            "coding": [{"display": patient_data["coverage_type"]}],
            "text": patient_data["coverage_type"],
        }
    if patient_data.get("member_id"):
        resource["subscriberId"] = patient_data["member_id"]
    return full_url, resource


def _make_condition_resource(patient_data: dict, patient_url: str) -> tuple[str, dict]:
    """Build a FHIR Condition resource with SNOMED + ICD-10 codings."""
    full_url = _urn()
    codings = []
    snomed = patient_data.get("condition_snomed", "")
    if snomed:
        codings.append({
            "system": "http://snomed.info/sct",
            "code": snomed,
            "display": patient_data.get("condition_display", ""),
        })
    icd_info = SNOMED_TO_ICD10.get(snomed)
    if icd_info:
        codings.append({
            "system": "http://hl7.org/fhir/sid/icd-10-cm",
            "code": icd_info[0],
            "display": icd_info[1],
        })

    resource: dict = {
        "resourceType": "Condition",
        "subject": {"reference": patient_url},
        "code": {"coding": codings, "text": patient_data.get("condition_display", "")},
    }
    if patient_data.get("condition_onset"):
        resource["onsetDateTime"] = patient_data["condition_onset"]
    return full_url, resource


def _make_medication_request(fields: dict[str, str], patient_url: str, practitioner_url: str) -> tuple[str, dict] | None:
    """Build a FHIR MedicationRequest for the requested drug."""
    drug_name = fields.get("requested_drug", "")
    if not drug_name:
        return None

    full_url = _urn()
    codings = [{"display": drug_name}]
    hcpcs = DRUG_TO_HCPCS.get(drug_name.lower(), "")
    if hcpcs:
        codings.append({
            "system": "http://www.ama-assn.org/go/cpt",
            "code": hcpcs,
            "display": drug_name,
        })

    resource: dict = {
        "resourceType": "MedicationRequest",
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {"coding": codings, "text": drug_name},
        "subject": {"reference": patient_url},
        "requester": {"reference": practitioner_url},
    }
    dose = fields.get("requested_dose", "")
    if dose:
        resource["dosageInstruction"] = [{"text": dose}]
    return full_url, resource


def _make_medication_statements(met_criteria: list[dict], patient_url: str) -> list[tuple[str, dict]]:
    """Build MedicationStatement resources for prior therapy drugs."""
    seen_drugs: set[str] = set()
    statements: list[tuple[str, dict]] = []

    for crit in met_criteria:
        if not crit.get("is_prior_therapy"):
            continue
        drug_name = crit.get("drug_name", "")
        if not drug_name or drug_name.lower() in seen_drugs:
            continue
        seen_drugs.add(drug_name.lower())

        full_url = _urn()
        resource: dict = {
            "resourceType": "MedicationStatement",
            "status": "completed",
            "medicationCodeableConcept": {"text": drug_name},
            "subject": {"reference": patient_url},
        }
        if crit.get("drug_dates"):
            resource["effectivePeriod"] = {"start": crit["drug_dates"]}
        if crit.get("failure_reason"):
            resource["note"] = [{"text": crit["failure_reason"]}]
        if crit.get("drug_dose"):
            resource["dosage"] = [{"text": crit["drug_dose"]}]
        statements.append((full_url, resource))

    return statements


def _make_questionnaire_response(
    met_criteria: list[dict],
    overrides: dict[str, bool],
    all_criteria: dict,
    patient_url: str,
) -> tuple[str, dict]:
    """Build a QuestionnaireResponse mapping criteria to boolean + evidence items."""
    full_url = _urn()
    items: list[dict] = []

    # Collect effective criteria: met from extraction + overrides
    effective_ids: set[str] = set()
    for crit in met_criteria:
        cid = crit.get("criterion_id", "")
        if cid:
            effective_ids.add(cid)
    # Apply overrides: True adds, False removes
    for cid, is_met in overrides.items():
        if is_met:
            effective_ids.add(cid)
        else:
            effective_ids.discard(cid)

    evidence_map = {c.get("criterion_id", ""): c.get("evidence", "") for c in met_criteria}

    for cid in sorted(effective_ids):
        leaf = all_criteria.get(cid)
        criterion_text = leaf.source_text if leaf else cid
        evidence = evidence_map.get(cid, "")

        item: dict = {
            "linkId": cid,
            "text": criterion_text,
            "answer": [
                {"valueBoolean": True},
            ],
        }
        if evidence:
            item["answer"].append({"valueString": evidence})
        items.append(item)

    resource = {
        "resourceType": "QuestionnaireResponse",
        "status": "completed",
        "subject": {"reference": patient_url},
        "item": items,
    }
    return full_url, resource


def build_pas_bundle(
    patient_data: dict,
    fields: dict[str, str],
    met_criteria: list[dict],
    overrides: dict[str, bool],
    tree,
    justification_text: str | None = None,
) -> dict:
    """Construct a Da Vinci PAS FHIR Bundle (type: collection).

    Returns a dict ready to be serialized as JSON.
    """
    all_criteria = get_all_criteria(tree)
    entries: list[dict] = []

    # Build individual resources
    patient_url, patient_res = _make_patient_resource(patient_data)
    practitioner_url, practitioner_res = _make_practitioner_resource(patient_data)
    org_url, org_res = _make_organization_resource(patient_data)
    coverage_url, coverage_res = _make_coverage_resource(patient_data, patient_url)
    condition_url, condition_res = _make_condition_resource(patient_data, patient_url)

    med_request_result = _make_medication_request(fields, patient_url, practitioner_url)
    med_request_url = med_request_result[0] if med_request_result else None

    med_statements = _make_medication_statements(met_criteria, patient_url)
    qr_url, qr_res = _make_questionnaire_response(met_criteria, overrides, all_criteria, patient_url)

    # Build Claim (must be first entry)
    claim_url = _urn()
    claim_items: list[dict] = []
    if med_request_url:
        claim_items.append({
            "sequence": 1,
            "productOrService": {
                "text": fields.get("requested_drug", ""),
            },
        })
    # Add ICD-10 diagnosis to Claim
    diagnosis_entries: list[dict] = []
    snomed = patient_data.get("condition_snomed", "")
    icd_info = SNOMED_TO_ICD10.get(snomed)
    if icd_info:
        diagnosis_entries.append({
            "sequence": 1,
            "diagnosisReference": {"reference": condition_url},
        })

    claim_res: dict = {
        "resourceType": "Claim",
        "status": "active",
        "type": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/claim-type",
                "code": "pharmacy",
            }],
        },
        "use": "preauthorization",
        "patient": {"reference": patient_url},
        "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "provider": {"reference": practitioner_url},
        "insurer": {"reference": org_url},
        "priority": {"coding": [{"code": "normal"}]},
        "insurance": [{
            "sequence": 1,
            "focal": True,
            "coverage": {"reference": coverage_url},
        }],
        "item": claim_items or [{"sequence": 1, "productOrService": {"text": "Prior Authorization Request"}}],
    }
    if diagnosis_entries:
        claim_res["diagnosis"] = diagnosis_entries
    if justification_text:
        claim_res["supportingInfo"] = [{
            "sequence": 1,
            "category": {
                "coding": [{"system": "http://hl7.org/fhir/us/davinci-pas/CodeSystem/PASSupportingInfoType", "code": "freeFormMessage"}],
            },
            "valueString": justification_text,
        }]

    # Assemble entries — Claim first per PAS spec
    entries.append({"fullUrl": claim_url, "resource": claim_res})
    entries.append({"fullUrl": patient_url, "resource": patient_res})
    entries.append({"fullUrl": practitioner_url, "resource": practitioner_res})
    entries.append({"fullUrl": org_url, "resource": org_res})
    entries.append({"fullUrl": coverage_url, "resource": coverage_res})
    entries.append({"fullUrl": condition_url, "resource": condition_res})
    if med_request_result:
        entries.append({"fullUrl": med_request_result[0], "resource": med_request_result[1]})
    for ms_url, ms_res in med_statements:
        entries.append({"fullUrl": ms_url, "resource": ms_res})
    entries.append({"fullUrl": qr_url, "resource": qr_res})

    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "entry": entries,
    }
    return bundle


@router.post("/form/generate-pas-bundle")
def generate_pas_bundle(request: GeneratePasBundleRequest):
    """Generate a Da Vinci PAS FHIR Bundle JSON for the patient."""
    bundle_path = _find_fhir_bundle(request.uuid)
    if not bundle_path:
        raise HTTPException(status_code=404, detail="No FHIR bundle found")

    patient_data = load_patient_from_fhir(bundle_path)
    tree = load_tree(TREE_PATH)
    fields = {f.field_id: f.value for f in request.fields}

    bundle = build_pas_bundle(
        patient_data=patient_data,
        fields=fields,
        met_criteria=request.met_criteria,
        overrides=request.overrides,
        tree=tree,
        justification_text=request.justification_text,
    )

    patient_name = patient_data["name"].replace(" ", "_")
    logger.info("[PAS] Generated FHIR bundle for %s with %d entries", patient_name, len(bundle["entry"]))

    return JSONResponse(
        content=bundle,
        headers={"Content-Disposition": f'attachment; filename="pas_bundle_{patient_name}.json"'},
    )
