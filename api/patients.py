"""Patient discovery and FHIR data endpoints."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.schemas import PatientSummary, PatientFhir, NotesResponse, NoteFile
from patient_data import load_patient_from_fhir

router = APIRouter()

NOTES_ROOT = Path("soap_notes")        # legacy
NOTES_ROOT_NEW = Path("notes")         # new multi-note structure
FHIR_ROOT = Path("generations")
FHIR_BUNDLED = Path("fhir")           # pre-selected bundles for deployment
EXTRACTIONS_DIR = Path(".")
UUID_PATTERN = re.compile(
    r"^(?P<name>.+)_(?P<uuid>[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\.txt$"
)


def _find_fhir_bundle(uuid: str) -> str | None:
    """Search for a FHIR bundle matching this UUID.

    Search order:
    1. fhir/ (bundled for deployment — checked first, fast)
    2. generations/ (full local dataset)
    """
    for json_file in FHIR_BUNDLED.glob(f"*{uuid}*.json"):
        return str(json_file)
    if not FHIR_ROOT.exists():
        return None
    for region_dir in FHIR_ROOT.iterdir():
        fhir_dir = region_dir / "fhir"
        if not fhir_dir.exists():
            continue
        for json_file in fhir_dir.glob(f"*{uuid}*.json"):
            return str(json_file)
    return None


def _discover_patients() -> dict[str, dict]:
    """Scan notes/ (new structure) and soap_notes/ (legacy) for patients.

    New structure takes priority: notes/{uuid}/metadata.json
    Legacy fallback: soap_notes/*.txt (old filename-encoded UUID format)
    """
    patients: dict[str, dict] = {}

    # --- New structure: notes/{uuid}/metadata.json ---
    if NOTES_ROOT_NEW.exists():
        for metadata_file in NOTES_ROOT_NEW.glob("*/metadata.json"):
            try:
                meta = json.loads(metadata_file.read_text(encoding="utf-8"))
                uuid = meta.get("uuid", "")
                name = meta.get("patient_name", "")
                if not uuid or not name:
                    continue
                files = [n["filename"] for n in meta.get("notes", [])]
                patients[uuid] = {"name": name, "uuid": uuid, "files": files}
            except (json.JSONDecodeError, KeyError):
                continue

    # --- Legacy fallback: soap_notes/*.txt ---
    if NOTES_ROOT.exists():
        for txt_file in sorted(NOTES_ROOT.glob("*.txt")):
            m = UUID_PATTERN.match(txt_file.name)
            if not m:
                continue
            uuid = m.group("uuid")
            if uuid in patients:
                continue  # Already covered by new structure
            name = m.group("name").replace("_", " ")
            if uuid not in patients:
                patients[uuid] = {"name": name, "uuid": uuid, "files": []}
            patients[uuid]["files"].append(txt_file.name)

    return patients


def _resolve_uuid(uuid_or_prefix: str) -> str:
    """Resolve a UUID or short prefix to a full UUID. Raises 404 if not found."""
    patients = _discover_patients()
    # Exact match first
    if uuid_or_prefix in patients:
        return uuid_or_prefix
    # Prefix match
    matches = [u for u in patients if u.startswith(uuid_or_prefix)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise HTTPException(status_code=400, detail="Ambiguous UUID prefix — multiple matches")
    raise HTTPException(status_code=404, detail="Patient not found")


def _find_eligibility(uuid: str) -> dict:
    """Look up pre-computed extraction eligibility for a patient."""
    for json_file in EXTRACTIONS_DIR.glob("extraction_*.json"):
        try:
            data = json.loads(json_file.read_text())
            entries = data if isinstance(data, list) else [data]
            for entry in entries:
                if entry.get("uuid", "").startswith(uuid[:8]):
                    return {
                        "eligible": entry.get("eligible"),
                        "met_count": entry.get("met_count"),
                        "total_count": entry.get("total_count"),
                    }
        except (json.JSONDecodeError, KeyError):
            continue
    return {"eligible": None, "met_count": None, "total_count": None}


@router.get("/patients", response_model=list[PatientSummary])
def list_patients():
    patients = _discover_patients()
    result = []
    for uuid, info in patients.items():
        if _find_fhir_bundle(uuid) is None:
            continue
        elig = _find_eligibility(uuid)
        result.append(
            PatientSummary(
                uuid=uuid,
                name=info["name"],
                files=info["files"],
                has_fhir=True,
                eligible=elig["eligible"],
                met_count=elig["met_count"],
                total_count=elig["total_count"],
            )
        )
    return sorted(result, key=lambda p: p.name)


@router.get("/patients/{uuid}/fhir", response_model=PatientFhir)
def get_patient_fhir(uuid: str):
    from patient_data import SNOMED_TO_ICD10, ENCOUNTER_CLASS_TO_LOCATION

    full_uuid = _resolve_uuid(uuid)
    bundle_path = _find_fhir_bundle(full_uuid)
    if not bundle_path:
        raise HTTPException(status_code=404, detail="No FHIR bundle found for this patient")
    data = load_patient_from_fhir(bundle_path)

    # Compute derived fields from FHIR data
    icd_info = SNOMED_TO_ICD10.get(data.get("condition_snomed", ""))
    if icd_info:
        data["icd10_code"] = icd_info[0]
        data["icd_version"] = "ICD-10"
    data["place_of_service"] = ENCOUNTER_CLASS_TO_LOCATION.get(
        data.get("encounter_class", ""), ""
    )

    return PatientFhir(**data)


@router.get("/patients/{uuid}/notes", response_model=NotesResponse)
def get_patient_notes(uuid: str):
    full_uuid = _resolve_uuid(uuid)

    # --- New structure: notes/{uuid}/metadata.json ---
    new_dir = NOTES_ROOT_NEW / full_uuid
    metadata_file = new_dir / "metadata.json"
    if metadata_file.exists():
        try:
            meta = json.loads(metadata_file.read_text(encoding="utf-8"))
            notes_meta = meta.get("notes", [])
            # Sort by date descending (newest first)
            notes_meta_sorted = sorted(notes_meta, key=lambda n: n.get("date", ""), reverse=True)
            files = []
            for note_meta in notes_meta_sorted:
                filename = note_meta.get("filename", "")
                note_path = new_dir / filename
                if note_path.exists():
                    content = note_path.read_text(encoding="utf-8")
                    files.append(NoteFile(
                        filename=filename,
                        content=content,
                        type=note_meta.get("type", "soap"),
                        date=note_meta.get("date", ""),
                        title=note_meta.get("title", filename),
                    ))
            return NotesResponse(files=files)
        except (json.JSONDecodeError, KeyError):
            pass

    # --- Legacy fallback: soap_notes/*.txt ---
    patients = _discover_patients()
    info = patients.get(full_uuid)
    if not info:
        raise HTTPException(status_code=404, detail="Patient not found")
    files = []
    for filename in info["files"]:
        path = NOTES_ROOT / filename
        if path.exists():
            content = path.read_text(encoding="utf-8")
            files.append(NoteFile(filename=filename, content=content))
    return NotesResponse(files=files)
