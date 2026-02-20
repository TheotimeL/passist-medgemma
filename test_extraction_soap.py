"""Evidence extraction using MedGemma (MLX local) with few-shot examples.

Reads SOAP clinical notes and evaluates them against a policy decision tree.
Uses a local MedGemma model via MLX for inference with low-temperature sampling.

Pipeline:
  1. Load enriched policy tree (keywords/anti_keywords for validation)
     and original tree (short search_descriptions for prompt)
  2. Build few-shot prompt with synthetic clinical note example
  3. Run MedGemma inference with chain-of-thought reasoning
  4. Parse JSON output and validate evidence against note text
  5. Evaluate against policy decision tree for eligibility determination
"""

import os

if "HF_TOKEN" not in os.environ:
    raise SystemExit("ERROR: HF_TOKEN environment variable is required. Set it in .env or export it.")

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from policy_tree import load_tree, get_all_criteria, CriterionResult, get_status

# ── Config ──────────────────────────────────────────────────────────
MODEL_ID = "mlx-community/medgemma-27b-text-it-bf16"
# Use enriched tree for keywords/anti_keywords validation, but original short descriptions for prompt
TREE_PATH = "rheumatoid_arthritis_initial_auth_decision_tree_enriched.json"
TREE_PATH_ORIGINAL = "rheumatoid_arthritis_initial_auth_decision_tree.json"
NOTES_ROOT = Path("soap_notes")
# Named shortcuts for quick testing (optional; all patients are auto-discovered)
ALL_TEST_UUIDS = {
    "vanesa":     "520e6e72-a964-eaf5-8495-b41c77eff668",  # Vanesa Sindy Thiel
    "broderick":  "2d701350-9a03-786d-661e-5cb4971efc20",  # Broderick Hackett
    "aide":       "5c9df1d3-2a63-1c68-b5fa-5ab79be51dad",  # Aide Reichel
    "caridad":    "92181936-5f66-8913-309f-7eb45e5fc388",  # Caridad Cecilia Zaragoza
    "alycia":     "aa20b461-6893-f4f8-cfce-4399c8065fe6",  # Alycia Obdulia Hodkiewicz
    "analisa":    "affb0758-feb8-4754-2fc9-15b54d6b398b",  # Analisa Auer
    "antonio":    "1a691f1f-6957-0fd7-cb3a-c0cc61c93371",  # Antonio Tello
    "abram":      "8b8f1e13-b68f-5d5f-4230-0ac867267002",  # Abram Gerlach
}

# ── Load criteria ───────────────────────────────────────────────────
# Enriched tree has keywords/anti_keywords for validation
tree = load_tree(TREE_PATH)
leaf_nodes = get_all_criteria(tree)
# Original tree has shorter search_descriptions for prompt (prevents repetition loops)
tree_original = load_tree(TREE_PATH_ORIGINAL)
leaf_nodes_original = get_all_criteria(tree_original)
all_criteria = [
    {
        "id": cid,
        "source_text": node.source_text or node.summary or node.name,
        # Use ORIGINAL short descriptions for prompt, enriched keywords for validation
        "search_description": leaf_nodes_original[cid].search_description if cid in leaf_nodes_original else node.search_description,
        "keywords": node.keywords,
        "anti_keywords": node.anti_keywords,
        "negated": node.negated,
    }
    for cid, node in leaf_nodes.items()
]
# Split: negated criteria are auto-met (absence = satisfied), only send non-negated to the model
criteria = [c for c in all_criteria if not c.get("negated")]
negated_criteria = [c for c in all_criteria if c.get("negated")]
print(f"Loaded {len(all_criteria)} criteria ({len(criteria)} for model, {len(negated_criteria)} negated/auto-met)")

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
from mlx_lm.models.cache import make_prompt_cache
from mlx_lm.generate import generate_step
import mlx.core as mx

model, tokenizer = load(str(MODEL_ID))
print("Model loaded.")

# Low temperature sampler for deterministic clinical reasoning
from mlx_lm.sample_utils import make_sampler, make_logits_processors
SAMPLER = make_sampler(temp=0.1, top_p=0.9)
LOGITS_PROCESSORS = make_logits_processors(repetition_penalty=1.2)

# ── Synthetic few-shot example ──────────────────────────────────────
# Fully generic clinical note — no disease-specific terms, works for any policy
EXAMPLE_NOTE = """SOAP NOTE — Specialist Clinic Visit

Patient: Jane Doe
DOB: 03/15/1965
Date of Visit: 01/10/2026
Provider: Dr. Robert Chen, MD, Department of Specialist Medicine, Verilex Medical Group

SUBJECTIVE:
Ms. Doe is a 60-year-old woman with a chronic inflammatory condition diagnosed June 2018, currently moderate-to-severe. She reports ongoing symptoms despite two prior medication trials.

OBJECTIVE:
Disease Activity Score: 4.8 (moderate-to-severe)
Labs: Inflammatory markers elevated.

ASSESSMENT:
1. Chronic Inflammatory Condition - Active, Confirmed. Disease Activity Score 4.8.
   - Zorbitamol 20 mg weekly: Started June 2018, discontinued October 2018 due to inadequate response
   - Plaxivent 1000 mg BID: Started November 2018, discontinued March 2019 (GI intolerance)
   - Crendolux and Mythafen: never tried

PLAN:
1. Continue current supportive care.
2. Consider escalation to targeted therapy at next visit.

Electronically signed,
Dr. Robert Chen, MD
Department of Specialist Medicine
Verilex Medical Group
01/10/2026"""


def _build_example_output(criteria: list[dict]) -> str:
    """Build a systematic reasoning + JSON example output for the synthetic note.

    Goes through EVERY criterion in order, showing MET or NOT MET for each.
    This teaches the model to evaluate all criteria systematically.
    Matches criteria by search_description content (generic clinical terms),
    not by policy-specific IDs.
    """
    reasoning_lines = []
    results = []
    matched = set()  # track which categories we've matched

    for c in criteria:
        cid = c['id']
        desc = (c.get('search_description') or c.get('source_text') or '').lower()

        if 'diagnosis' not in matched and ('diagnosis' in desc or 'disease activity' in desc):
            reasoning_lines.append(f"{cid}: Active confirmed, DAS 4.8. MET.")
            results.append({"criterion_id": cid, "met": True,
                            "evidence": "Chronic Inflammatory Condition - Active, Confirmed. Disease Activity Score 4.8."})
            matched.add('diagnosis')
        elif 'drug_failure' not in matched and ('fail' in desc or 'inadequate response' in desc):
            reasoning_lines.append(f"{cid}: Zorbitamol discontinued (inadequate response), Plaxivent discontinued (GI intolerance). MET.")
            results.append({"criterion_id": cid, "met": True,
                            "evidence": "Zorbitamol 20 mg weekly: Started June 2018, discontinued October 2018 due to inadequate response\nPlaxivent 1000 mg BID: Started November 2018, discontinued March 2019 (GI intolerance)",
                            "drug_name": "Zorbitamol, Plaxivent",
                            "drug_dose": "Zorbitamol 20 mg weekly, Plaxivent 1000 mg BID",
                            "drug_dates": "Zorbitamol June 2018 - October 2018, Plaxivent November 2018 - March 2019",
                            "is_prior_therapy": True,
                            "failure_reason": "Zorbitamol: inadequate response; Plaxivent: GI intolerance"})
            matched.add('drug_failure')
        elif 'prescriber' not in matched and ('specialist' in desc or 'prescrib' in desc):
            reasoning_lines.append(f"{cid}: Provider: Dr. Robert Chen, MD, Department of Specialist Medicine, Verilex Medical Group. MET.")
            results.append({"criterion_id": cid, "met": True,
                            "evidence": "Provider: Dr. Robert Chen, MD, Department of Specialist Medicine, Verilex Medical Group",
                            "prescriber_name": "Dr. Robert Chen, MD",
                            "prescriber_specialty": "Specialist Medicine"})
            matched.add('prescriber')
        else:
            reasoning_lines.append(f"{cid}: NOT MET.")

    reasoning = "\n".join(reasoning_lines)
    json_output = json.dumps(results)
    return f"{reasoning}\n\nJSON:\n{json_output}"


def _build_criteria_block(criteria: list[dict]) -> str:
    lines = []
    for c in criteria:
        desc = c.get('search_description', '').strip() or c.get('source_text', '').strip()
        lines.append(f"- {c['id']}: {desc}")
    return "\n".join(lines)


def _build_prompt_prefix(criteria: list[dict]) -> str:
    """Static prefix: instructions + criteria + example (identical across all patients)."""
    criteria_block = _build_criteria_block(criteria)
    example_output = _build_example_output(criteria)

    return (
        "<start_of_turn>user\n"
        "You are an expert clinical consultant reviewing a patient document against insurance policy criteria.\n\n"
        "TASK: For each criterion below, find evidence in the document. Output met criteria as JSON.\n\n"
        "OUTPUT FORMAT:\n"
        "1. Go through EVERY criterion in order. For each one:\n"
        "   - If MET: write criterion ID + brief evidence quote + MET.\n"
        "   - If NOT MET: write criterion ID + NOT MET.\n"
        "2. After all criteria are evaluated, write JSON: followed by a JSON array of met criteria only.\n"
        "3. CRITICAL: The JSON MUST include EVERY criterion you marked as MET. Missing items = error.\n\n"
        "JSON FIELDS (per criterion):\n"
        "- criterion_id (required): the criterion ID\n"
        "- met (required): boolean\n"
        "- evidence (required): exact text from the document\n"
        "- drug_name (optional): specific drug mentioned, e.g. 'methotrexate', 'adalimumab'\n"
        "- drug_dose (optional): dosage if mentioned, e.g. '20 mg weekly'\n"
        "- drug_dates (optional): therapy dates if mentioned, e.g. 'June 2018 - October 2018'\n"
        "- is_prior_therapy (optional): true if evidence describes a drug previously tried/failed/discontinued; false if about current or newly requested therapy\n"
        "- failure_reason (optional): brief reason for discontinuation, e.g. 'inadequate response', 'hepatotoxicity'\n"
        "- prescriber_name (optional): provider name if this is a prescriber criterion\n"
        "- prescriber_specialty (optional): provider specialty, e.g. 'Rheumatology'\n\n"
        "RULES:\n"
        "1. Evidence must be EXACT text from the document. Do not paraphrase or add words.\n"
        "2. Each criterion needs its OWN evidence that specifically mentions what that criterion asks about.\n"
        "   Drug failure for Drug X requires evidence mentioning Drug X by name.\n"
        "3. For prescriber evidence: extract the provider line or signature block WITH name, department/specialty, and practice.\n"
        "4. If a criterion is not clearly supported, OMIT it. Most criteria will NOT be met.\n"
        "5. For drug-related criteria, always include drug_name, is_prior_therapy, and failure_reason when applicable.\n\n"
        "CRITERIA:\n"
        f"{criteria_block}\n\n"
        "DOCUMENT:\n"
        f"{EXAMPLE_NOTE}\n"
        "<end_of_turn>\n"
        "<start_of_turn>model\n"
        f"{example_output}\n"
        "<end_of_turn>\n"
        "<start_of_turn>user\n"
        "Now extract evidence from a COMPLETELY DIFFERENT patient document below.\n"
        "CRITICAL: Use ONLY text from THIS new document. The previous example is irrelevant.\n"
        "Do NOT copy any evidence from the example above — it is a different patient.\n\n"
        "DOCUMENT:\n"
    )


def _build_prompt_suffix(note_text: str) -> str:
    """Dynamic suffix: patient note + model turn start (changes per patient)."""
    return (
        f"{note_text}\n"
        "<end_of_turn>\n"
        "<start_of_turn>model\n"
        "REASONING:\n"
    )


def _run_inference(suffix: str, max_tokens: int = 8000) -> str:
    """Run inference using cached prefix + new suffix."""
    cache = _copy.deepcopy(_PREFIX_CACHE)

    suffix_tokens = tokenizer.encode(suffix, add_special_tokens=False)
    output = generate(model, tokenizer, prompt=suffix_tokens, max_tokens=max_tokens,
                      verbose=False, prompt_cache=cache,
                      sampler=SAMPLER, logits_processors=LOGITS_PROCESSORS)
    return output.strip()


def _run_inference_full(prompt: str, max_tokens: int = 8000) -> str:
    """Run inference without caching (for repair prompts)."""
    output = generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False,
                      sampler=SAMPLER, logits_processors=LOGITS_PROCESSORS)
    return output.strip()


# ── Prompt cache ──────────────────────────────────────────────────
# Pre-compute KV cache for the static prompt prefix (instructions + criteria + example).
# This prefix is identical for all patients; only the patient note (suffix) changes.
# We deepcopy the cached state before each inference call to get a clean prefix state.
import copy as _copy

print("Caching prompt prefix...")
t_cache = time.time()
PROMPT_PREFIX = _build_prompt_prefix(criteria)
_prefix_tokens = mx.array(tokenizer.encode(PROMPT_PREFIX))
_PREFIX_CACHE = make_prompt_cache(model)
for _ in generate_step(_prefix_tokens, model, max_tokens=0, prompt_cache=_PREFIX_CACHE):
    pass
# Force evaluation so deepcopy gets materialized arrays
mx.eval(*[kv.state for kv in _PREFIX_CACHE])
print(f"Prefix cached: {_PREFIX_CACHE[0].offset} tokens ({time.time() - t_cache:.1f}s)")


def _truncate_repetition(text: str) -> str:
    """Detect and truncate repetitive model output.

    When the model degenerates into repeating the same phrase, this cuts it off
    at the start of the repetition, preserving the useful content before it.
    """
    # Look for any sentence-like chunk (20+ chars) that appears 3+ times
    # Search from the end backwards to find where repetition starts
    lines = text.split('\n')
    for line_len in range(1, 4):  # check 1-3 line chunks
        for i in range(len(lines) - line_len * 3):
            chunk = '\n'.join(lines[i:i + line_len]).strip()
            if len(chunk) < 20:
                continue
            # Count occurrences in the rest of the text
            rest = '\n'.join(lines[i:])
            count = rest.count(chunk)
            if count >= 3:
                # Truncate at the second occurrence
                first_end = text.index(chunk) + len(chunk)
                return text[:first_end].rstrip()
    return text


def _parse_json_response(raw: str) -> list[dict] | None:
    # First truncate any repetition loops
    raw = _truncate_repetition(raw)
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



def _keyword_in_text(keyword: str, text: str) -> bool:
    """Check if keyword appears in text, with word boundary matching for short keywords.

    Short keywords (<=3 chars like 'im', 'pa', 'fm', 'gp', 'np') must match as
    whole words to avoid false positives (e.g., 'im' matching inside 'Kim').
    Longer keywords use substring matching as before.
    """
    kw_lower = keyword.lower()
    text_lower = text.lower()
    if len(kw_lower) <= 3:
        return bool(re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower))
    return kw_lower in text_lower


def _evidence_grounding_ratio(evidence: str, note: str) -> float:
    """Check what fraction of evidence words appear as contiguous 4-gram matches in the note.

    This catches fabricated or paraphrased evidence: if the model outputs text that
    doesn't appear as contiguous sequences in the note, it's likely hallucinated.
    For example, evidence saying 'DO (Rheumatology)' when the note says 'DO, Family Medicine'
    will have low grounding because the 4-grams around the fabricated part won't match.

    Returns a ratio from 0.0 (no grounding) to 1.0 (fully grounded).
    Evidence shorter than 5 words is too short to check meaningfully and returns 1.0.
    """
    def _norm(t: str) -> str:
        t = t.lower()
        t = re.sub(r'[^\w\s]', ' ', t)
        return re.sub(r'\s+', ' ', t).strip()

    ev = _norm(evidence)
    nt = _norm(note)
    words = ev.split()
    if len(words) < 5:
        return 1.0

    grounded = set()
    for i in range(len(words) - 3):
        four_gram = ' '.join(words[i:i + 4])
        if four_gram in nt:
            for j in range(i, i + 4):
                grounded.add(j)

    return len(grounded) / len(words)


def validate_evidence(parsed: list[dict], criteria: list[dict], note_text: str) -> list[dict]:
    """Minimal structural validation — let the LLM do the heavy lifting."""
    criteria_by_id = {c['id']: c for c in criteria}
    filtered = []

    # Detect evidence spam: same text used for 3+ different criteria
    evidence_counts = {}
    for item in parsed:
        if item.get("met") is True:
            ev = (item.get("evidence") or "").strip().lower()
            if ev and 'no mention' not in ev:
                evidence_counts[ev] = evidence_counts.get(ev, 0) + 1
    spammed = {ev for ev, n in evidence_counts.items() if n >= 3}

    for item in parsed:
        if not isinstance(item, dict):
            continue

        cid = item.get("criterion_id", "")
        met = item.get("met")
        evidence = (item.get("evidence") or "").strip()

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
            print(f"  [SPAM] {cid[:60]} — evidence reused for {evidence_counts[evidence_lower]} criteria")
            continue

        # N-gram grounding: check if evidence appears as contiguous sequences in the note.
        # This catches fabricated evidence (e.g., model swaps "Family Medicine" for "Rheumatology")
        # more reliably than word-level overlap which misses contextual fabrication.
        grounding = _evidence_grounding_ratio(evidence, note_text)
        if grounding < 0.5:
            print(f"  [LOW_GROUNDING] {cid[:60]} — only {grounding:.0%} of evidence is contiguous in note")
            continue

        # Reject empty or very short evidence
        if len(evidence) < 10:
            print(f"  [TOO SHORT] {cid[:60]} — evidence only {len(evidence)} chars")
            continue

        # Keyword validation: if the criterion has anti_keywords, check if evidence
        # contains them (red flag for false positives).
        # Logic: if evidence has MORE anti_keywords than positive keywords, flag it.
        anti_kws = criterion_info.get('anti_keywords', [])
        pos_kws = criterion_info.get('keywords', [])
        if anti_kws and pos_kws and not is_negated:
            found_anti = [kw for kw in anti_kws if _keyword_in_text(kw, evidence)]
            found_pos = [kw for kw in pos_kws if _keyword_in_text(kw, evidence)]
            if found_anti and len(found_anti) >= len(found_pos):
                print(f"  [ANTI_KW] {cid[:60]} — anti({len(found_anti)})≥pos({len(found_pos)}): {found_anti}")
                continue

        # Note-grounding: if criterion has keywords, at least one must appear in the actual note
        # Catches hallucinated evidence that mentions the right drug but the drug isn't in the note
        if pos_kws and not is_negated:
            found_in_note = [kw for kw in pos_kws if _keyword_in_text(kw, note_text)]
            if not found_in_note:
                print(f"  [NOT_IN_NOTE] {cid[:60]} — none of {pos_kws[:3]} found in note")
                continue

        # Note-level anti-keyword cross-check: if the note contains anti_keywords
        # and evidence lacks positive keywords, the evidence is likely wrong.
        if anti_kws and pos_kws and not is_negated:
            found_anti_in_note = [kw for kw in anti_kws if _keyword_in_text(kw, note_text)]
            if found_anti_in_note:
                found_pos_in_evidence = [kw for kw in pos_kws if _keyword_in_text(kw, evidence)]
                if not found_pos_in_evidence:
                    print(f"  [NOTE_CONTRA] {cid[:60]} — note has {found_anti_in_note[:3]} but evidence lacks positive keywords")
                    continue

        # Source-text entity grounding: for criteria with short source_text (drug names
        # or specific entities), evidence must mention that entity by name.
        # Catches drug-criterion mismatches (e.g., hydroxychloroquine evidence mapped
        # to the sulfasalazine criterion).
        source_text_raw = criterion_info.get('source_text', '')
        if source_text_raw and len(source_text_raw) < 50 and not is_negated:
            source_words = set(re.findall(r'\b\w{4,}\b', source_text_raw.lower()))
            # Exclude generic clinical terms — only keep entity-specific words
            _generic = {'patient', 'history', 'therapy', 'treatment', 'failure', 'trial',
                        'documented', 'received', 'currently', 'previously', 'been', 'with',
                        'that', 'have', 'from', 'this', 'prior', 'date', 'drug'}
            specific_words = source_words - _generic
            if specific_words:
                found_in_ev = [w for w in specific_words if w in evidence_lower]
                if not found_in_ev:
                    print(f"  [WRONG_ENTITY] {cid[:60]} — evidence missing {list(specific_words)[:3]}")
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

    suffix = _build_prompt_suffix(pat["text"])

    # Run inference twice and merge results for reliability (handles JSON truncation)
    all_validated = {}  # criterion_id -> best item (deduped)
    total_inference_time = 0

    for run_idx in range(2):
        print(f"Running inference (run {run_idx + 1}/2)...")
        t0 = time.time()
        raw = _run_inference(suffix, max_tokens=8000)
        run_time = time.time() - t0
        total_inference_time += run_time
        print(f"  Run {run_idx + 1} time: {run_time:.1f}s")

        print(f"\n--- RAW OUTPUT run {run_idx + 1} (first 3000 chars) ---")
        print(raw[:3000])
        print(f"--- END RAW (total {len(raw)} chars) ---\n")

        parsed = _parse_json_response(raw)
        if parsed is None:
            print("FAILED to parse JSON! Attempting repair...")
            repair_prompt = (
                "<start_of_turn>user\n"
                "Convert the following clinical evidence analysis into a valid JSON array.\n"
                "Each item: {criterion_id: string, met: boolean, evidence: string}.\n"
                "Output ONLY the JSON array, nothing else.\n\n"
                f"{raw}\n"
                "<end_of_turn>\n<start_of_turn>model\n["
            )
            raw2 = "[" + _run_inference_full(repair_prompt)
            parsed = _parse_json_response(raw2)

        if parsed:
            met_items = [i for i in parsed if i.get("met") is True]
            print(f"  Run {run_idx + 1}: {len(met_items)} met criteria (before validation)")

            validated = validate_evidence(parsed, all_criteria, pat["text"])
            for item in validated:
                cid = item.get("criterion_id", "")
                if cid not in all_validated:
                    all_validated[cid] = item
                elif len(item.get("evidence", "")) > len(all_validated[cid].get("evidence", "")):
                    all_validated[cid] = item  # keep longer evidence
        else:
            print(f"  Run {run_idx + 1}: FAILED to parse JSON")

    inference_time = total_inference_time
    filtered = list(all_validated.values())

    if filtered or negated_criteria:
        print(f"\n=== MERGED RESULTS ({len(filtered)} unique criteria from 2 runs) ===")

        # Add negated criteria as auto-met (absence = satisfied)
        for nc in negated_criteria:
            if nc["id"] not in all_validated:
                filtered.append({"criterion_id": nc["id"], "met": True, "evidence": "No mention found"})
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
                {k: v for k, v in item.items() if v is not None and v != ""}
                for item in filtered
            ],
            "met_count": status.met_count,
            "total_count": status.total_count,
            "inference_time_s": round(inference_time, 1),
        }
    else:
        print("FAILED to parse JSON from both runs!")
        return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run evidence extraction on SOAP notes")
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

    # Print timing summary
    print(f"\n{'='*60}")
    print("TIMING SUMMARY")
    print(f"{'='*60}")
    total_time = sum(r.get("inference_time_s", 0) for r in all_results)
    for r in all_results:
        t = r.get("inference_time_s", 0)
        elig = "ELIGIBLE" if r["eligible"] is True else "NOT ELIGIBLE" if r["eligible"] is False else "PENDING"
        print(f"  {r['name']:<35} {t:6.1f}s  {r['met_count']:2d}/{r['total_count']} met  {elig}")
    print(f"  {'TOTAL':<35} {total_time:6.1f}s")

    # Write JSON output if requested
    if args.output:
        with open(args.output, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\n✓ Results written to {args.output} ({len(all_results)} patients)")
