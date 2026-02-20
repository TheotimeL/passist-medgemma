"""FHIR-based patient data extraction and PA form filling.

Provides:
- load_patient_from_fhir(): Parse a Synthea FHIR bundle into a structured dict
- format_section_vi(): Format extraction evidence + FHIR data into clinical justification
- fill_pa_form(): Orchestrate FHIR data + extraction → filled PA form PDF

All processing is LOCAL — no external API calls. Code mappings use static tables.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from policy_tree import PolicyNode, PolicyStatus, get_all_criteria


# ---------------------------------------------------------------------------
# Local code mapping tables (no external API calls)
# ---------------------------------------------------------------------------

SNOMED_TO_ICD10: dict[str, tuple[str, str]] = {
    "69896004": ("M06.9", "Rheumatoid arthritis, unspecified"),
    "239873007": ("M05.79", "Rheumatoid arthritis with rheumatoid factor, unspecified site"),
    "287007002": ("M05.70", "Seropositive rheumatoid arthritis, multiple sites"),
    "201791009": ("M05.79", "Flare of rheumatoid arthritis"),
    "410795001": ("M05.70", "Erosive rheumatoid arthritis"),
}

# FHIR encounter class → human-readable location name
ENCOUNTER_CLASS_TO_LOCATION: dict[str, str] = {
    "AMB": "Provider Office",
    "IMP": "Inpatient",
    "EMER": "Outpatient",
    "HH": "Home",
    "SS": "Day Surgery",
}

DRUG_TO_HCPCS: dict[str, str] = {
    "adalimumab": "J0135",
    "etanercept": "J1438",
    "certolizumab": "J0717",
    "golimumab": "J1602",
    "abatacept": "J0129",
    "tofacitinib": "J3590",
    "baricitinib": "J3590",
    "upadacitinib": "J3590",
}




def _infer_requested_drug(tree: PolicyNode) -> str:
    """Infer the drug being requested for PA from the policy tree.

    Scans non-step-therapy leaf criteria (combination therapy, current user)
    for drug names in their source_text. The drug mentioned there is typically
    the one being requested, not a prior/step therapy drug.
    """
    # Common non-drug words to skip
    skip = {
        "monotherapy", "therapy", "currently", "taking", "receiving",
        "prescribed", "continues", "ongoing", "active", "current",
        "combination", "treatment", "documented", "single",
    }
    all_crit = get_all_criteria(tree)
    for cid, leaf in all_crit.items():
        # Skip step therapy criteria (they mention prior drugs, not the requested one)
        if "step" in cid and "current" not in cid:
            continue
        for kw in leaf.keywords:
            kw_lower = kw.lower()
            if (
                len(kw.split()) == 1
                and 4 <= len(kw) <= 25  # min 4 chars to avoid abbreviations like "ra"
                and kw_lower not in skip
                and kw_lower in leaf.source_text.lower()
            ):
                return kw.title()
    return ""


def _extract_specialty_from_tree(tree: PolicyNode) -> str:
    """Extract prescriber specialty from tree criteria keywords."""
    all_crit = get_all_criteria(tree)
    for cid, leaf in all_crit.items():
        if "prescrib" not in cid:
            continue
        # First single-word keyword > 5 chars is likely the specialty
        for kw in leaf.keywords:
            if len(kw.split()) == 1 and len(kw) > 5:
                return kw.title()
    return ""


def _clean_evidence(evidence: str) -> str:
    """Clean evidence text for use in clinical prose."""
    if not evidence or evidence in ("Not mentioned in note", "No mention found", "NOTHING"):
        return ""
    clean = evidence.replace("\n", " ").strip()
    if len(clean) > 300:
        clean = clean[:297] + "..."
    return clean


def _calculate_age(dob: str) -> int | None:
    """Calculate age from ISO date string."""
    if not dob:
        return None
    try:
        birth = datetime.strptime(dob[:10], "%Y-%m-%d")
        today = datetime.now()
        return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# FHIR Bundle Parser
# ---------------------------------------------------------------------------

def _parse_npi(reference: str) -> str | None:
    """Extract NPI from a FHIR conditional reference string.

    Example: 'Practitioner?identifier=http://hl7.org/fhir/sid/us-npi|9999985390'
    Returns: '9999985390'
    """
    match = re.search(r"us-npi\|(\d+)", reference)
    return match.group(1) if match else None


def _parse_date(iso_str: str) -> str:
    """Extract date portion from an ISO datetime string.

    Example: '2024-04-15T21:54:49+02:00' → '2024-04-15'
    """
    return iso_str[:10] if iso_str else ""


def load_patient_from_fhir(bundle_path: str) -> dict:
    """Extract PA-relevant fields from a Synthea FHIR bundle.

    Parses Patient, Encounter, Condition, MedicationRequest, and
    ExplanationOfBenefit resources to build a structured dict suitable
    for PA form filling.

    Args:
        bundle_path: Path to a Synthea FHIR bundle JSON file.

    Returns:
        Dict with patient demographics, provider info, condition,
        medications, coverage, and encounter details.
    """
    with open(bundle_path) as f:
        bundle = json.load(f)

    # Index resources by type
    resources: dict[str, list[dict]] = {}
    for entry in bundle.get("entry", []):
        r = entry.get("resource", {})
        rt = r.get("resourceType", "")
        resources.setdefault(rt, []).append(r)

    result: dict = {
        "name": "",
        "dob": "",
        "gender": "",
        "phone": "",
        "member_id": "",
        "address": "",
        "provider_name": "",
        "provider_npi": "",
        "facility_name": "",
        "condition_snomed": "",
        "condition_display": "",
        "condition_onset": "",
        "medications": [],
        "coverage_type": "",
        "insurer": "",
        "latest_encounter_date": "",
        "encounter_class": "",
    }

    # --- Patient ---
    patients = resources.get("Patient", [])
    if patients:
        pat = patients[0]
        # Name
        names = pat.get("name", [])
        official = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
        given = " ".join(official.get("given", []))
        family = official.get("family", "")
        result["name"] = f"{given} {family}".strip()

        # DOB, gender
        result["dob"] = pat.get("birthDate", "")
        result["gender"] = pat.get("gender", "")

        # Phone
        telecoms = pat.get("telecom", [])
        if telecoms:
            result["phone"] = telecoms[0].get("value", "")

        # Member ID (MRN identifier)
        for ident in pat.get("identifier", []):
            id_type = ident.get("type", {})
            codings = id_type.get("coding", [])
            if any(c.get("code") == "MR" for c in codings):
                result["member_id"] = ident.get("value", "")
                break

        # Address
        addrs = pat.get("address", [])
        if addrs:
            a = addrs[0]
            line = ", ".join(a.get("line", []))
            city = a.get("city", "")
            state = a.get("state", "")
            postal = a.get("postalCode", "")
            parts = [p for p in [line, city, state, postal] if p]
            result["address"] = ", ".join(parts)

    # --- Encounters (sorted by date, latest first) ---
    encounters = resources.get("Encounter", [])
    if encounters:
        encounters_sorted = sorted(
            encounters,
            key=lambda e: e.get("period", {}).get("start", ""),
            reverse=True,
        )
        latest = encounters_sorted[0]

        # Provider name + NPI from participant
        participants = latest.get("participant", [])
        if participants:
            individual = participants[0].get("individual", {})
            result["provider_name"] = individual.get("display", "")
            ref = individual.get("reference", "")
            npi = _parse_npi(ref)
            if npi:
                result["provider_npi"] = npi

        # Facility name from serviceProvider
        sp = latest.get("serviceProvider", {})
        result["facility_name"] = sp.get("display", "")

        # Encounter date and class
        result["latest_encounter_date"] = _parse_date(
            latest.get("period", {}).get("start", "")
        )
        result["encounter_class"] = latest.get("class", {}).get("code", "")

    # --- Condition ---
    conditions = resources.get("Condition", [])
    if conditions:
        cond = conditions[0]
        coding = cond.get("code", {}).get("coding", [{}])[0]
        result["condition_snomed"] = coding.get("code", "")
        result["condition_display"] = coding.get("display", "")
        result["condition_onset"] = _parse_date(cond.get("onsetDateTime", ""))

    # --- MedicationRequest ---
    for med_req in resources.get("MedicationRequest", []):
        med_concept = med_req.get("medicationCodeableConcept", {})
        coding = med_concept.get("coding", [{}])[0]
        result["medications"].append({
            "name": coding.get("display", ""),
            "rxnorm": coding.get("code", ""),
            "status": med_req.get("status", ""),
            "authored_on": _parse_date(med_req.get("authoredOn", "")),
        })

    # --- ExplanationOfBenefit (coverage + insurer) ---
    eobs = resources.get("ExplanationOfBenefit", [])
    if eobs:
        eob = eobs[0]
        # Insurer display
        result["insurer"] = eob.get("insurer", {}).get("display", "")
        # Coverage type from contained resources
        for contained in eob.get("contained", []):
            if contained.get("resourceType") == "Coverage":
                result["coverage_type"] = contained.get("type", {}).get("text", "")
                break

    return result


# ---------------------------------------------------------------------------
# Section VI Evidence Formatter
# ---------------------------------------------------------------------------

def _get_branch_nodes(tree: PolicyNode) -> dict[str, PolicyNode]:
    """Get the top-level branch nodes (direct children of root's first AND child).

    Returns {branch_id: PolicyNode} for each branch (e.g., diagnosis, step therapy).
    """
    branches = {}
    root = tree
    if root.type.value == "ROOT" and root.children:
        for top_child in root.children:
            for branch in top_child.children:
                if branch.id:
                    branches[branch.id] = branch
    return branches


def format_section_vi(
    patient_data: dict,
    policy_status: PolicyStatus,
    extraction_results: list[dict],
    tree: PolicyNode | None = None,
) -> str:
    """Format extraction evidence + FHIR data as a clinical letter of medical necessity.

    Produces natural clinical prose suitable for a PA form's justification field,
    written in the style a physician would use. All content is derived dynamically
    from the policy tree, FHIR data, and MedGemma extraction — no disease-specific
    hardcoding.

    Uses \\r\\n line endings for proper PDF AcroForm multiline rendering.

    Args:
        patient_data: Dict from load_patient_from_fhir().
        policy_status: PolicyStatus from policy_tree.get_status().
        extraction_results: List of met criteria dicts with 'criterion_id' and 'evidence'.
        tree: PolicyNode tree for accessing branch names and keywords.

    Returns:
        Formatted clinical justification string (PDF-compatible line endings).
    """
    NL = "\r\n"
    evidence_by_id = {r["criterion_id"]: r.get("evidence", "") for r in extraction_results}
    all_criteria = get_all_criteria(tree) if tree else {}

    # Dynamic values from FHIR + tree
    drug_name = _infer_requested_drug(tree) if tree else ""
    condition = patient_data["condition_display"] or "the diagnosed condition"
    icd_info = SNOMED_TO_ICD10.get(patient_data["condition_snomed"])
    icd_str = f" (ICD-10: {icd_info[0]})" if icd_info else ""
    onset = patient_data["condition_onset"]
    age = _calculate_age(patient_data["dob"])
    gender_word = {"female": "female", "male": "male"}.get(patient_data["gender"], "")

    parts: list[str] = []

    # --- Letter header ---
    drug_label = f" - {drug_name}" if drug_name else ""
    parts.append(f"RE: Prior Authorization Request{drug_label}")
    parts.append(f"Patient: {patient_data['name']} | DOB: {patient_data['dob']} | ID: {patient_data['member_id']}")
    parts.append("")
    parts.append("To Whom It May Concern,")
    parts.append("")

    # --- Opening paragraph ---
    age_str = f"{age}-year-old {gender_word}" if age else gender_word
    drug_phrase = f" for {drug_name.lower()}" if drug_name else ""
    onset_str = f", diagnosed {onset}" if onset else ""
    parts.append(
        f"I am writing to request prior authorization{drug_phrase} for my patient, "
        f"{patient_data['name']}, a {age_str} with {condition}{icd_str}{onset_str}."
    )
    parts.append("")

    # --- Evidence paragraphs grouped by branch (only met branches) ---
    branches_seen: set[str] = set()
    branch_nodes = _get_branch_nodes(tree) if tree else {}

    for criterion in policy_status.criteria:
        cid = criterion["id"]
        branch_key = ".".join(cid.split(".")[:2])

        if branch_key in branches_seen:
            continue
        branches_seen.add(branch_key)

        branch_criteria = [
            c for c in policy_status.criteria if c["id"].startswith(branch_key)
        ]
        met_criteria = [c for c in branch_criteria if c["status"] == "met"]

        if not met_criteria:
            continue  # Skip unmet branches — doctor can add context manually

        branch_node = branch_nodes.get(branch_key)
        branch_label = branch_node.name if branch_node else branch_key

        evidence_pieces: list[str] = []
        for mc in met_criteria:
            ev = evidence_by_id.get(mc["id"], mc.get("evidence", ""))
            ev_clean = _clean_evidence(ev)
            if ev_clean:
                evidence_pieces.append(ev_clean)

        if evidence_pieces:
            evidence_text = ". ".join(evidence_pieces)
            if not evidence_text.endswith("."):
                evidence_text += "."
            parts.append(f"Regarding {branch_label.lower()}: {evidence_text}")
        else:
            parts.append(
                f"Regarding {branch_label.lower()}: no contradicting evidence found."
            )
        parts.append("")

    # --- Closing ---
    parts.append(
        f"Based on the above, {policy_status.met_count} of "
        f"{policy_status.total_count} policy criteria are satisfied. "
        f"I respectfully request approval for my patient."
    )
    parts.append("")

    # --- Signature block ---
    parts.append("Sincerely,")
    if patient_data["provider_name"]:
        parts.append(patient_data["provider_name"])
        if patient_data["provider_npi"]:
            parts.append(f"NPI: {patient_data['provider_npi']}")
    if patient_data["facility_name"]:
        parts.append(patient_data["facility_name"])

    return NL.join(parts)


# ---------------------------------------------------------------------------
# Form Fill Orchestrator
# ---------------------------------------------------------------------------

def _set(pdf, fields: dict[str, str]) -> int:
    """Set multiple form fields at once, silently skipping missing ones.

    Returns the number of fields that were actually set.
    """
    count = 0
    for field_id, value in fields.items():
        if value and field_id in pdf.fields_map:
            pdf.fields_map[field_id]["value"] = value
            count += 1
    return count


def fill_pa_form(
    pdf,  # PDFFormManager instance
    patient_data: dict,
    policy_status: PolicyStatus,
    extraction_results: list[dict],
    tree: PolicyNode | None = None,
    output_path: str | None = None,
) -> str:
    """Map FHIR patient data + extraction results to PA form fields and generate PDF.

    All drug names and clinical context are derived dynamically from the policy
    tree and FHIR data — no disease-specific hardcoding.

    Uses the BCBS TX form layout (nofr002.pdf).

    Args:
        pdf: PDFFormManager instance (already loaded with the PA form template).
        patient_data: Dict from load_patient_from_fhir().
        policy_status: PolicyStatus from policy_tree.get_status().
        extraction_results: List of met criteria dicts with 'criterion_id' and 'evidence'.
        tree: PolicyNode tree for dynamic drug/specialty extraction.
        output_path: Output PDF path. Defaults to 'pa_form_{patient_name}.pdf'.

    Returns:
        Path to the generated PDF file.
    """
    evidence_by_id = {r["criterion_id"]: r.get("evidence", "") for r in extraction_results}
    all_criteria = get_all_criteria(tree) if tree else {}
    icd_info = SNOMED_TO_ICD10.get(patient_data["condition_snomed"])
    icd_code = icd_info[0] if icd_info else ""
    icd_desc = icd_info[1] if icd_info else patient_data["condition_display"]

    # Infer requested drug and specialty from the tree (no hardcoding)
    requested_drug = _infer_requested_drug(tree) if tree else ""
    hcpcs_code = DRUG_TO_HCPCS.get(requested_drug.lower(), "") if requested_drug else ""

    # Prescriber specialty: prefer LLM-extracted, fall back to tree inference
    prescriber_specialty = ""
    for ext in extraction_results:
        spec = ext.get("prescriber_specialty", "")
        if spec:
            prescriber_specialty = spec
            break
    if not prescriber_specialty:
        prescriber_specialty = _extract_specialty_from_tree(tree) if tree else ""

    # Parse address components from FHIR (format: "street, city, state, zip")
    addr = patient_data.get("address", "")
    addr_parts = [p.strip() for p in addr.split(",")]
    addr_street = addr_parts[0] if len(addr_parts) > 0 else ""
    addr_city = addr_parts[1] if len(addr_parts) > 1 else ""
    addr_state = addr_parts[2] if len(addr_parts) > 2 else ""
    addr_zip = addr_parts[3] if len(addr_parts) > 3 else ""

    # --- Gender checkbox ---
    gender = patient_data["gender"]
    gender_field = {
        "female": "Patient's Gender - Female",
        "male": "Patient's Gender - Male",
        "other": "Patient's Gender - Other",
        "unknown": "Patient's Gender - Unknown",
    }.get(gender, "")

    # --- Set all fields (BCBS naming conventions) ---
    filled = 0

    # Patient demographics
    filled += _set(pdf, {
        "Patient's Name": patient_data["name"],
        "Patient's Date of Birth": patient_data["dob"],
        "Patient's Phone Number": patient_data["phone"],
        "Member or Medicaid ID Number": patient_data["member_id"],
        "Patient's Address - Street": addr_street,
        "Patient's Address - City": addr_city,
        "Patient's Address - State": addr_state,
        "Patient's Address - ZIP Code": addr_zip,
    })
    if gender_field:
        filled += _set(pdf, {gender_field: "/On"})

    # Provider info (BCBS field names)
    filled += _set(pdf, {
        "Prescriber's Name": patient_data["provider_name"],
        "Prescriber's NPI Number": patient_data["provider_npi"],
        "Prescriber's Specialty": prescriber_specialty,
    })

    # Diagnosis (BCBS field names)
    filled += _set(pdf, {
        "Patients diagnosis related to this request": f"{patient_data['condition_display']}, onset {patient_data['condition_onset']}",
        "ICD Code": icd_code,
        "ICD Version": "ICD-10",
    })

    # Extract requested drug dose from LLM extraction
    requested_dose = ""
    for ext in extraction_results:
        if ext.get("is_prior_therapy") is False and ext.get("drug_dose"):
            requested_dose = ext["drug_dose"]
            break

    # Requested drug details (BCBS field names)
    filled += _set(pdf, {
        "Requested Prescription Drug Name": requested_drug,
        "Requested Prescription Drug Strength": requested_dose,
        "For Provider Administered Drugs Only - HCPCS Code": hcpcs_code,
        "New therapy": "/On",
    })

    # Administrative (BCBS field names)
    today = datetime.now().strftime("%m/%d/%Y")
    filled += _set(pdf, {
        "Date Submitted": today,
        "Submitted to": patient_data.get("insurer", ""),
    })

    # --- Drug history table (BCBS has 6 rows for prior medications) ---
    # Uses LLM-provided fields (drug_name, is_prior_therapy, failure_reason)
    # from extraction results. Falls back to tree keyword extraction if
    # the LLM didn't provide structured drug fields.
    extraction_by_id = {r["criterion_id"]: r for r in extraction_results}
    drug_row = 1
    for criterion in policy_status.criteria:
        if drug_row > 6:
            break
        cid = criterion["id"]
        if criterion["status"] != "met":
            continue

        ext = extraction_by_id.get(cid, {})
        evidence = ext.get("evidence", criterion.get("evidence", ""))
        if not evidence or evidence == "No mention found":
            continue

        # Use LLM-provided is_prior_therapy to decide what goes in drug history
        is_prior = ext.get("is_prior_therapy")
        if is_prior is False:
            continue  # LLM says this is NOT prior therapy — skip

        # Drug name: require LLM-provided drug_name for drug history entries.
        # Keyword extraction can't reliably distinguish drug names from disease names,
        # so we trust the LLM to identify which criteria are about specific drugs.
        drug_name = ext.get("drug_name", "")
        if not drug_name:
            continue

        # Failure reason: prefer LLM-provided, fall back to evidence text
        failure = ext.get("failure_reason", "")
        if not failure:
            failure = evidence[:200]

        # Drug dose and dates from LLM if available
        drug_dose = ext.get("drug_dose", "")
        drug_dates = ext.get("drug_dates", "")

        # BCBS drug history field names
        filled += _set(pdf, {
            f"Drugs Patient has Taken for Diagnosis - Drug Name {drug_row}": drug_name,
            f"Strength of Drug {drug_row}": drug_dose,
            f"Dates Started and Stopped or Approximate Duration of Drug {drug_row}": drug_dates,
            f"Describe Response Reason for Failure or Allergy of Drug {drug_row}": failure,
        })
        drug_row += 1

    # --- Clinical justification (BCBS Section IX) ---
    section_vi = format_section_vi(patient_data, policy_status, extraction_results, tree)
    filled += _set(pdf, {
        "Section IX \u2015 Justification (See Instruction Page Section IX)": section_vi,
    })

    # --- Generate PDF ---
    if output_path is None:
        safe_name = patient_data["name"].replace(" ", "_")
        output_path = f"pa_form_{safe_name}.pdf"

    pdf.generate_pdf(output_path)
    print(f"Filled {filled} fields")
    return output_path
