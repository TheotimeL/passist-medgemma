"""Evidence extraction using MedGemma (MLX local) with few-shot examples.

Reads clinical notes and evaluates them against a policy decision tree.
Uses a local MedGemma model via MLX for inference with low-temperature sampling.
"""

import os

if "HF_TOKEN" not in os.environ:
    raise SystemExit("ERROR: HF_TOKEN environment variable is required. Set it in .env or export it.")

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
from policy_tree import load_tree, get_all_criteria, CriterionResult, get_status

# ── Config ──────────────────────────────────────────────────────────
MODEL_ID = "mlx-community/medgemma-27b-text-it-bf16"
TREE_PATH = "rheumatoid_arthritis_initial_auth_decision_tree.json"
NOTES_ROOT = Path("clinical_notes")
# Named shortcuts for quick testing (optional; all patients are auto-discovered)
ALL_TEST_UUIDS = {
    "ardella":  "31d9fa9f-0639-05ea-eaff-5bf8aa742139",  # Ardella Cartwright
}

# ── Load criteria ───────────────────────────────────────────────────
tree = load_tree(TREE_PATH)
leaf_nodes = get_all_criteria(tree)
criteria = [
    {
        "id": cid,
        "source_text": node.source_text or node.summary or node.name,
        "search_description": node.search_description,
        "negated": node.negated,
    }
    for cid, node in leaf_nodes.items()
]
print(f"Loaded {len(criteria)} criteria")

# ── Load patients ──────────────────────────────────────────────────
UUID_PATTERN = re.compile(
    r"^(?P<name>.+)_(?P<uuid>[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\.txt$"
)
patients = {}
if NOTES_ROOT.exists():
    for txt_file in sorted(NOTES_ROOT.glob("*.txt")):
        m = UUID_PATTERN.match(txt_file.name)
        if not m:
            continue
        uuid = m.group("uuid")
        patient_name = m.group("name").replace("_", " ")
        rel_path = str(txt_file.relative_to(NOTES_ROOT))
        if uuid not in patients:
            patients[uuid] = {"name": patient_name, "uuid": uuid, "files": [], "texts": []}
        patients[uuid]["files"].append(rel_path)
        patients[uuid]["texts"].append(txt_file.read_text(encoding="utf-8"))

for p in patients.values():
    p["text"] = "\n\n---\n\n".join(p["texts"])
    del p["texts"]

print(f"Loaded {len(patients)} patients")

# ── Load model ──────────────────────────────────────────────────────
print("Loading model...")
from mlx_lm import load, generate

model, tokenizer = load(str(MODEL_ID))
print("Model loaded.")

# Low temperature sampler for deterministic clinical reasoning
from mlx_lm.sample_utils import make_sampler, make_logits_processors
SAMPLER = make_sampler(temp=0.1, top_p=0.9)
LOGITS_PROCESSORS = make_logits_processors(repetition_penalty=1.1)

# ── Synthetic few-shot example ──────────────────────────────────────
# Fully generic clinical note — no disease-specific terms, works for any policy
EXAMPLE_NOTE = """DISCHARGE SUMMARY

Patient Name: Jane Doe
Date of Birth: March 15, 1965
Attending Physician: Dr. Robert Chen

ACTIVE PROBLEMS
1. Chronic Inflammatory Condition - Active, Confirmed
   Onset: June 2018
   Disease Activity Score: 4.8 (moderate-to-severe)

MEDICATION HISTORY
- Drug A 20 mg weekly: Started June 2018, discontinued October 2018 due to inadequate response
- Drug B 1000 mg BID: Started November 2018, discontinued March 2019 (GI intolerance)

CURRENT MEDICATIONS
1. Drug C 50 mg subcutaneous injection every 2 weeks
   Status: Active

ALLERGIES
No Known Drug Allergies (NKDA)

Electronically signed,
Dr. Robert Chen, MD
Specialty Medicine Division
January 10, 2026"""


def _build_example_output(criteria: list[dict]) -> str:
    """Build a JSON example output for the synthetic note.

    Uses structural heuristics (criterion ID patterns, negated flag) instead of
    disease-specific string matching, so this works for any policy tree.
    Picks up to ~5 criteria to demonstrate the expected format.
    """
    results = []
    matched_diagnosis = False
    matched_drug_failure = 0
    matched_prescriber = False

    for c in criteria:
        cid = c['id']
        negated = c.get('negated', False)

        # Match first diagnosis-like criterion
        if not matched_diagnosis and 'diagnosis' in cid:
            results.append({"criterion_id": cid, "met": True,
                            "evidence": "Chronic Inflammatory Condition - Active, Confirmed"})
            matched_diagnosis = True
        # Match up to 2 drug failure criteria (show Drug A and Drug B)
        elif matched_drug_failure < 2 and not negated and ('failure' in cid or 'dmard' in cid):
            if matched_drug_failure == 0:
                results.append({"criterion_id": cid, "met": True,
                                "evidence": "Drug A 20 mg weekly: Started June 2018, discontinued October 2018 due to inadequate response"})
            else:
                results.append({"criterion_id": cid, "met": True,
                                "evidence": "Drug B 1000 mg BID: Started November 2018, discontinued March 2019 (GI intolerance)"})
            matched_drug_failure += 1
        # Match prescriber criterion
        elif not matched_prescriber and 'prescri' in cid:
            results.append({"criterion_id": cid, "met": True,
                            "evidence": "Dr. Robert Chen, MD\nSpecialty Medicine Division"})
            matched_prescriber = True
        # Negated criteria → "No mention found"
        elif negated:
            results.append({"criterion_id": cid, "met": True, "evidence": "No mention found"})
        # Everything else: skip (demonstrates that unmet criteria are omitted)

    return json.dumps(results, indent=2)


def _build_criteria_block(criteria: list[dict]) -> str:
    lines = []
    for c in criteria:
        # Prefer search_description (Gemini-generated, plain English) over raw source_text
        desc = c.get('search_description', '').strip() or c.get('source_text', '').strip()
        negated = c.get('negated', False)
        neg_tag = " [NEGATED]" if negated else ""
        lines.append(f"- {c['id']}: {desc}{neg_tag}")
    return "\n".join(lines)


def _build_prompt(note_text: str, criteria: list[dict]) -> str:
    criteria_block = _build_criteria_block(criteria)
    example_output = _build_example_output(criteria)

    return (
        "<start_of_turn>user\n"
        "You are an expert clinical consultant reviewing a patient document against insurance policy criteria.\n"
        "For each criterion, determine if there is clear evidence in the document.\n"
        "Output a JSON array with ONLY the criteria that are clearly met. Skip all criteria that are not met.\n"
        "Evidence must be EXACT text copied from the document.\n\n"
        "RULES:\n"
        "- For [NEGATED] criteria: met=true when the document has NO mention. Use evidence='No mention found'.\n"
        "- Each evidence must be specific to THAT criterion. Do not reuse the same text for multiple criteria.\n"
        "- If a criterion is not clearly met, do NOT include it in the output.\n\n"
        "CRITERIA:\n"
        f"{criteria_block}\n\n"
        "DOCUMENT:\n"
        f"{EXAMPLE_NOTE}\n"
        "<end_of_turn>\n"
        "<start_of_turn>model\n"
        f"{example_output}\n"
        "<end_of_turn>\n"
        "<start_of_turn>user\n"
        "Now extract evidence from this NEW document below. Only use text from THIS document, never from the previous example.\n\n"
        "DOCUMENT:\n"
        f"{note_text}\n"
        "<end_of_turn>\n"
        "<start_of_turn>model\n"
    )


def _run_inference(prompt: str, max_tokens: int = 4000) -> str:
    output = generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False,
                      sampler=SAMPLER, logits_processors=LOGITS_PROCESSORS)
    return output.strip()


def _parse_json_response(raw: str) -> list[dict] | None:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```\s*$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _extract_words(text: str) -> set:
    common = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "is", "in", "on", "or", "of", "to", "not", "has", "been", "any", "found", "mention", "met", "true", "false"}
    words = re.findall(r'\b\w{3,}\b', text.lower())
    return {w for w in words if w not in common}


def validate_evidence(parsed: list[dict], criteria: list[dict], note_text: str) -> list[dict]:
    """Minimal structural validation - let the LLM do the heavy lifting."""
    criteria_by_id = {c['id']: c for c in criteria}
    filtered = []

    # Detect evidence spam: same text used for 3+ different criteria
    evidence_counts = {}
    for item in parsed:
        if item.get("met") is True:
            ev = item.get("evidence", "").strip().lower()
            if ev and 'no mention' not in ev:
                evidence_counts[ev] = evidence_counts.get(ev, 0) + 1
    spammed = {ev for ev, n in evidence_counts.items() if n >= 3}

    for item in parsed:
        if not isinstance(item, dict):
            continue

        cid = item.get("criterion_id", "")
        met = item.get("met")
        evidence = item.get("evidence", "").strip()

        # Only keep met=true with non-empty evidence
        if met is not True or not evidence:
            continue

        criterion_info = criteria_by_id.get(cid, {})
        is_negated = criterion_info.get('negated', False)
        evidence_lower = evidence.lower()

        # "No mention found" only valid for negated criteria
        if 'no mention found' in evidence_lower:
            if is_negated:
                filtered.append(item)
            continue

        # Reject spammed evidence (same text reused for 3+ criteria)
        if evidence_lower in spammed:
            continue

        # Reject evidence not found in the actual patient note (hallucination from example)
        # Check that key content words from evidence appear in the note
        note_lower = note_text.lower()
        ev_words = _extract_words(evidence)
        note_words = _extract_words(note_text)
        if ev_words:
            overlap = ev_words & note_words
            overlap_ratio = len(overlap) / len(ev_words)
            # At least 50% of evidence words must appear in the note
            if overlap_ratio < 0.5:
                continue

        # Reject empty or very short evidence
        if len(evidence) < 10:
            continue

        filtered.append(item)

    return filtered


# ── Run smoke tests ────────────────────────────────────────────────
def run_smoke_test(uuid: str, patients: dict, criteria: list, tree):
    if uuid not in patients:
        print(f"Patient {uuid} not found!")
        return

    pat = patients[uuid]
    print(f"\n{'='*60}")
    print(f"Patient: {pat['name']} ({uuid[:8]}...)")
    print(f"{'='*60}\n")

    prompt = _build_prompt(pat["text"], criteria)
    print("Running inference...")
    raw = _run_inference(prompt, max_tokens=4000)

    print(f"\n--- RAW OUTPUT (first 1500 chars) ---")
    print(raw[:1500])
    print(f"--- END RAW (total {len(raw)} chars) ---\n")

    parsed = _parse_json_response(raw)
    if parsed is None:
        print("FAILED to parse JSON! Attempting repair...")
        repair_prompt = (
            prompt + raw
            + "\n<end_of_turn>\n<start_of_turn>user\n"
            + "Output ONLY the complete JSON array starting with [ and ending with ].\n"
            + "<end_of_turn>\n<start_of_turn>model\n"
        )
        raw2 = _run_inference(repair_prompt)
        parsed = _parse_json_response(raw2)

    if parsed:
        met_items = [i for i in parsed if i.get("met") is True]
        print(f"Model returned {len(met_items)} met criteria (before validation):")
        for item in met_items:
            ev = item.get("evidence", "")[:100]
            print(f"  {item.get('criterion_id', '')[:70]}")
            print(f"    -> {ev}")
        print()

        print("=== VALIDATION ===")
        filtered = validate_evidence(parsed, criteria, pat["text"])
        print()

        print(f"=== FINAL MET CRITERIA ({len(filtered)} items) ===")
        for item in filtered:
            print(f"  {item.get('criterion_id', '')[:70]}")
            print(f"    Evidence: {item.get('evidence', '')[:120]}")
        print()

        results = {}
        for item in filtered:
            cid = item["criterion_id"]
            results[cid] = CriterionResult(
                criterion_id=cid, met=True, evidence=item.get("evidence", ""), source_ref=pat["files"][0]
            )

        status = get_status(tree, results)
        print(f"Policy: Overall={status.overall}, Met={status.met_count}/{status.total_count}, Pending={status.pending_count}")
        for c in status.criteria:
            if c['status'] == 'met':
                print(f"  [MET] {c['id'][:70]}")

        # Return structured result
        return {
            "uuid": uuid,
            "name": pat["name"],
            "eligible": status.overall,
            "met_criteria": [
                {"criterion_id": item["criterion_id"], "evidence": item.get("evidence", "")}
                for item in filtered
            ],
            "met_count": status.met_count,
            "total_count": status.total_count,
        }
    else:
        print("FAILED to parse JSON even after repair!")
        print("Raw output:")
        print(raw[:500])
        return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run evidence extraction smoke tests")
    parser.add_argument("patients", nargs="*",
                        help=f"Patient names or UUIDs to test (default: all {len(patients)} discovered patients)")
    parser.add_argument("--output", type=str, default=None,
                        help="Path to output JSON file with results")
    args = parser.parse_args()

    # Determine which patients to run
    if args.patients:
        test_uuids = []
        for name in args.patients:
            name_lower = name.lower()
            if name_lower in ALL_TEST_UUIDS:
                test_uuids.append(ALL_TEST_UUIDS[name_lower])
            else:
                # Treat as raw UUID
                test_uuids.append(name)
    else:
        # Run all discovered patients
        test_uuids = sorted(patients.keys())

    # Run extraction for each patient and collect results
    all_results = []
    for test_uuid in test_uuids:
        result = run_smoke_test(test_uuid, patients, criteria, tree)
        if result is not None:
            all_results.append(result)

    # Write JSON output if requested
    if args.output:
        with open(args.output, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\n✓ Results written to {args.output} ({len(all_results)} patients)")
