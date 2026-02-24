"""ExtractionService — MedGemma extraction for web use.

Provides a singleton class that:
1. Loads MedGemma model once at startup
2. Caches the prompt prefix
3. Exposes a generator-based extract() method for SSE streaming
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Generator

# HF_TOKEN is read from environment if needed for MLX model download

from dataclasses import dataclass, field as dc_field

from config import TREE_PATH, TREE_PATH_ORIGINAL, NOTES_ROOT, NOTES_ROOT_NEW, UUID_PATTERN, POLICY, tree_paths
from .policy_tree import load_tree, get_all_criteria, CriterionResult, get_status

# Config
# MODEL_ID = "mlx-community/medgemma-27b-text-it-bf16"
# MODEL_ID = "mlx-community/medgemma-27b-text-it-4bit"
MODEL_ID = "mlx-community/medgemma-1.5-4b-it-bf16"

# ── Static few-shot example (fake criteria + note, decoupled from real policy) ──
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
2. Start Crendolux 200 mg subcutaneous every 2 weeks.

Electronically signed,
Dr. Robert Chen, MD
Department of Specialist Medicine
Verilex Medical Group
01/10/2026"""

# Static fake criteria for few-shot example (covers all common PA patterns)
EXAMPLE_CRITERIA = [
    {"id": "demo_A", "search_description": "Patient has a confirmed diagnosis of moderately to severely active chronic inflammatory condition"},
    {"id": "demo_B", "search_description": "Patient failed at least one conventional therapy: Zorbitamol, Plaxivent, or Crendolux. Evidence includes documented inadequate response, adverse effects, intolerance, or discontinuation."},
    {"id": "demo_C", "search_description": "Patient is currently receiving the requested medication as an active, ongoing therapy prior to this visit. Evidence: drug listed as current/active medication or documented continuation."},
    {"id": "demo_D", "search_description": "Patient is NOT taking another targeted therapy at the same time (met if there is NO mention of concurrent targeted therapy use)"},
    {"id": "demo_E", "search_description": "Prescribed by or in consultation with a relevant specialist. Evidence: prescriber identified as the required specialist (name, title, specialty) and drug in the treatment plan."},
]

EXAMPLE_OUTPUT = (
    "demo_A: Looking for confirmed diagnosis with severity level.\n"
    "  Found: \"Chronic Inflammatory Condition - Active, Confirmed.\" and \"Disease Activity Score: 4.8 (moderate-to-severe)\".\n"
    "  → MET\n\n"
    "demo_B: Looking for prior drug failure with drug name, dates, and reason.\n"
    "  Found: \"Zorbitamol 20 mg weekly: Started June 2018, discontinued October 2018 due to inadequate response\" and \"Plaxivent 1000 mg BID: Started November 2018, discontinued March 2019 (GI intolerance)\".\n"
    "  → MET\n\n"
    "demo_C: Looking for evidence patient is currently on the requested drug.\n"
    "  Found: \"Start Crendolux\" in Plan — this is a NEW prescription, not evidence of current use.\n"
    "  → NOT MET\n\n"
    "demo_D: Looking for concurrent targeted therapy use (negated — met if absent).\n"
    "  No mention of concurrent targeted therapy found.\n"
    "  → NOT MET\n\n"
    "demo_E: Looking for prescriber with relevant specialty.\n"
    "  Found: \"Start Crendolux 200 mg subcutaneous every 2 weeks.\" and \"Dr. Robert Chen, MD, Department of Specialist Medicine, Verilex Medical Group\".\n"
    "  → MET\n\n"
    "JSON:\n"
    + json.dumps([
        {"criterion_id": "demo_A", "met": True,
         "evidence": ["Chronic Inflammatory Condition - Active, Confirmed.", "Disease Activity Score: 4.8 (moderate-to-severe)"]},
        {"criterion_id": "demo_B", "met": True,
         "evidence": [
             "Zorbitamol 20 mg weekly: Started June 2018, discontinued October 2018 due to inadequate response",
             "Plaxivent 1000 mg BID: Started November 2018, discontinued March 2019 (GI intolerance)",
         ]},
        {"criterion_id": "demo_E", "met": True,
         "evidence": ["Start Crendolux 200 mg subcutaneous every 2 weeks.", "Dr. Robert Chen, MD, Department of Specialist Medicine, Verilex Medical Group"]},
    ])
)


# ── Shared prompt content (importable by benchmark_models.py) ──────────────────

SYSTEM_INSTRUCTIONS = (
    "You are an expert clinical consultant reviewing a patient document against insurance policy criteria.\n\n"
    "TASK: For each criterion below, find evidence in the document. Output met criteria as JSON.\n\n"
    "OUTPUT FORMAT:\n"
    "1. Go through EVERY criterion in order. For each one:\n"
    "   - If MET: write criterion ID + each relevant evidence quote (one per location found) + MET.\n"
    "   - If NOT MET: write criterion ID + NOT MET.\n"
    "2. After all criteria are evaluated, write JSON: followed by a JSON array of met criteria only.\n"
    "3. CRITICAL: The JSON MUST include EVERY criterion you marked as MET. Missing items = error.\n\n"
    "JSON FIELDS (per criterion):\n"
    "- criterion_id (required): the criterion ID\n"
    "- met (required): boolean\n"
    "- evidence (required): array of exacted text snippets from the document. "
    "When evidence spans multiple separate locations, include each snippet as a separate array element.\n"
    "\n"
    "RULES:\n"
    "1. Evidence must be EXACT text from the document. Do not paraphrase or add words.\n"
    "2. Each criterion needs its OWN evidence that specifically mentions what that criterion asks about.\n"
    "   Drug failure for Drug X requires evidence mentioning Drug X by name.\n"
    "3. If a criterion is not clearly supported, mark it NOT MET.\n"
    "4. When evidence for one criterion is found in multiple separate locations, include ALL relevant snippets as separate array elements.\n"
    "5. 'Start [drug]' or 'Prescribe [drug]' in the Plan section means a NEW prescription, not evidence the patient is already on that drug.\n"
    "6. Each evidence snippet should be 1-3 sentences copied verbatim from the document.\n"
    "   Include enough context that a reviewer can understand the finding.\n"
    "7. When a criterion asks about a drug, include ALL available details from the same\n"
    "   passage: name, dose, route, frequency, dates, outcome — whatever is present.\n"
    "   Do not split these into separate snippets; keep related details together.\n"
    "8. When a criterion asks about a prescriber, include the full provider identification\n"
    "   (name, credentials, specialty, facility) as a single snippet.\n"
    "9. Look for clinical synonyms and equivalent phrasings — the document may describe\n"
    "   the same concept using different terminology than the criterion. Match by meaning,\n"
    "   not just exact wording. But always QUOTE the document's own words as evidence."
)

TRANSITION_TEXT = (
    "Now extract evidence from a COMPLETELY DIFFERENT patient document below.\n"
    "CRITICAL: Use ONLY text from THIS new document.\n"
    "Do NOT copy any evidence from the example above — it is a different patient.\n"
    "Use ONLY the criterion IDs from the CRITERIA block below. Do NOT use any IDs from the example above (demo_A, demo_B, etc.)."
)

ASSISTANT_PREFIX = "I'll evaluate each criterion against this document.\n\n"


def _build_criteria_block(criteria: list[dict]) -> str:
    lines = []
    for c in criteria:
        desc = c.get('search_description', '').strip() or c.get('source_text', '').strip()
        lines.append(f"- {c['id']}: {desc}")
    return "\n".join(lines)


def _build_prompt_prefix(criteria: list[dict]) -> str:
    example_criteria_block = _build_criteria_block(EXAMPLE_CRITERIA)
    real_criteria_block = _build_criteria_block(criteria)

    return (
        "<start_of_turn>user\n"
        f"{SYSTEM_INSTRUCTIONS}\n\n"
        "CRITERIA:\n"
        f"{example_criteria_block}\n\n"
        "DOCUMENT:\n"
        f"{EXAMPLE_NOTE}\n"
        "<end_of_turn>\n"
        "<start_of_turn>model\n"
        f"{EXAMPLE_OUTPUT}\n"
        "<end_of_turn>\n"
        "<start_of_turn>user\n"
        f"{TRANSITION_TEXT}\n\n"
        "CRITERIA:\n"
        f"{real_criteria_block}\n\n"
        "DOCUMENT:\n"
    )


def _build_prompt_suffix(note_text: str) -> str:
    return (
        f"{note_text}\n"
        "<end_of_turn>\n"
        "<start_of_turn>model\n"
        f"{ASSISTANT_PREFIX}"
    )


def _truncate_repetition(text: str) -> str:
    lines = text.split('\n')
    for line_len in range(1, 4):
        for i in range(len(lines) - line_len * 3):
            chunk = '\n'.join(lines[i:i + line_len]).strip()
            if len(chunk) < 20:
                continue
            rest = '\n'.join(lines[i:])
            count = rest.count(chunk)
            if count >= 3:
                first_end = text.index(chunk) + len(chunk)
                return text[:first_end].rstrip()
    return text


def _normalize_evidence(item: dict) -> dict:
    """Normalize evidence field: array → evidence_snippets + joined evidence string."""
    ev = item.get("evidence")
    if isinstance(ev, list):
        snippets = [s.strip() for s in ev if isinstance(s, str) and s.strip()]
        item["evidence_snippets"] = snippets
        item["evidence"] = "\n".join(snippets)
    elif isinstance(ev, str):
        item["evidence_snippets"] = [ev.strip()] if ev.strip() else []
    else:
        item["evidence_snippets"] = []
        item["evidence"] = ""
    return item


def _parse_json_response(raw: str) -> list[dict] | None:
    raw = _truncate_repetition(raw)
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```\s*$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()
    parsed = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        pass
    if parsed is None:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    if parsed is not None and isinstance(parsed, list):
        return [_normalize_evidence(item) for item in parsed if isinstance(item, dict)]
    return None


def _keyword_in_text(keyword: str, text: str) -> bool:
    kw_lower = keyword.lower()
    text_lower = text.lower()
    if len(kw_lower) <= 3:
        return bool(re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower))
    return kw_lower in text_lower


def _deduplicate_keyword_matches(matched_keywords: list[str]) -> list[str]:
    """Remove keyword matches where one is a substring of another.

    When 'remission' and 'in remission' both match, they likely matched
    the same text region. Keep only the longest (most specific) match.
    """
    if len(matched_keywords) <= 1:
        return matched_keywords
    sorted_kws = sorted(matched_keywords, key=len, reverse=True)
    result = []
    for kw in sorted_kws:
        if not any(kw in kept for kept in result):
            result.append(kw)
    return result


def _evidence_grounding_ratio(evidence: str, note: str) -> float:
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


def validate_evidence(parsed: list[dict], all_criteria: list[dict], note_text: str) -> list[dict]:
    criteria_by_id = {c['id']: c for c in all_criteria}
    filtered = []

    # Spam detection: count per-snippet reuse across all criteria
    snippet_counts: dict[str, int] = {}
    for item in parsed:
        if item.get("met") is True:
            for snippet in item.get("evidence_snippets", []):
                sn = snippet.strip().lower()
                if sn and 'no mention' not in sn:
                    snippet_counts[sn] = snippet_counts.get(sn, 0) + 1
    spammed_snippets = {sn for sn, n in snippet_counts.items() if n >= 3}

    for item in parsed:
        if not isinstance(item, dict):
            continue

        cid = item.get("criterion_id", "")
        met = item.get("met")
        evidence = (item.get("evidence") or "").strip()
        snippets = item.get("evidence_snippets", [])

        if met is not True or not evidence:
            continue

        criterion_info = criteria_by_id.get(cid, {})
        is_negated = criterion_info.get('negated', False)
        evidence_lower = evidence.lower()

        if 'no mention found' in evidence_lower:
            if is_negated:
                filtered.append(item)
            continue

        # Per-snippet spam: reject only if ALL snippets are spammed
        if snippets and all(s.strip().lower() in spammed_snippets for s in snippets):
            continue

        # N-gram grounding at 30%: catches fully fabricated evidence while allowing
        # paraphrasing (e.g. abbreviation expansion like MTX→methotrexate).
        ratio = _evidence_grounding_ratio(evidence, note_text)
        if ratio < 0.3:
            continue

        if len(evidence) < 10:
            continue

        anti_kws = criterion_info.get('anti_keywords', [])
        pos_kws = criterion_info.get('keywords', [])
        if anti_kws and pos_kws and not is_negated:
            found_anti = [kw for kw in anti_kws if _keyword_in_text(kw, evidence)]
            found_anti = _deduplicate_keyword_matches(found_anti)
            found_pos = [kw for kw in pos_kws if _keyword_in_text(kw, evidence)]
            if found_anti and len(found_anti) > len(found_pos):
                continue

        if pos_kws and not is_negated and 'prescrib' not in cid:
            found_in_note = [kw for kw in pos_kws if _keyword_in_text(kw, note_text)]
            if not found_in_note:
                continue

        if anti_kws and pos_kws and not is_negated:
            found_anti_in_note = [kw for kw in anti_kws if _keyword_in_text(kw, note_text)]
            found_anti_in_note = _deduplicate_keyword_matches(found_anti_in_note)
            if found_anti_in_note:
                found_pos_in_evidence = [kw for kw in pos_kws if _keyword_in_text(kw, evidence)]
                if not found_pos_in_evidence:
                    continue

        source_text_raw = criterion_info.get('source_text', '')
        if source_text_raw and len(source_text_raw) < 50 and not is_negated:
            source_words = set(re.findall(r'\b\w{4,}\b', source_text_raw.lower()))
            _generic = {'patient', 'history', 'therapy', 'treatment', 'failure', 'trial',
                        'documented', 'received', 'currently', 'previously', 'been', 'with',
                        'that', 'have', 'from', 'this', 'prior', 'date', 'drug'}
            specific_words = source_words - _generic
            if specific_words:
                found_in_ev = [w for w in specific_words if w in evidence_lower]
                if not found_in_ev:
                    continue

        filtered.append(item)

    return filtered


def merge_note_results(
    per_note_results: list[tuple[str, list[dict]]],
    combined_text: str,
) -> dict[str, dict]:
    """Merge extraction results from multiple notes.

    For each criterion, keeps the best result (met=true wins).
    Unions evidence_snippets across notes (dedup by lowercased content).
    Attaches source_note from the winning note.

    Args:
        per_note_results: List of (filename, validated_items) per note.
        combined_text: Concatenated text of all notes (for validation fallback).

    Returns:
        Dict mapping criterion_id → best result dict (with source_note).
    """
    merged: dict[str, dict] = {}
    for filename, items in per_note_results:
        for item in items:
            cid = item.get("criterion_id", "")
            item_with_source = {**item, "source_note": filename}
            if cid not in merged:
                merged[cid] = item_with_source
            else:
                existing = merged[cid]
                # met=true always wins over met=false
                if item.get("met") and not existing.get("met"):
                    # Replace but carry over existing snippets
                    existing_snippets = existing.get("evidence_snippets", [])
                    merged[cid] = item_with_source
                    _union_snippets(merged[cid], existing_snippets)
                elif item.get("met") == existing.get("met"):
                    # Union snippets from new item into existing
                    new_snippets = item.get("evidence_snippets", [])
                    _union_snippets(existing, new_snippets)
                    # Rebuild joined evidence from unified snippets
                    existing["evidence"] = "\n".join(existing.get("evidence_snippets", []))
    return merged


def _union_snippets(target: dict, new_snippets: list[str]) -> None:
    """Merge new_snippets into target's evidence_snippets, deduping by lowercased content."""
    existing = target.get("evidence_snippets", [])
    seen = {s.strip().lower() for s in existing}
    for snippet in new_snippets:
        key = snippet.strip().lower()
        if key and key not in seen:
            existing.append(snippet)
            seen.add(key)
    target["evidence_snippets"] = existing


@dataclass
class PolicyCacheEntry:
    """Per-policy cached state: tree, criteria, and optional MLX KV prefix cache."""
    slug: str
    tree: object  # PolicyNode
    tree_original: object  # PolicyNode
    all_criteria: list[dict] = dc_field(default_factory=list)
    criteria: list[dict] = dc_field(default_factory=list)
    negated_criteria: list[dict] = dc_field(default_factory=list)
    prefix_cache: object = None  # MLX KV cache (list of cache objects)


class ExtractionService:
    """Singleton wrapping MedGemma extraction for web use."""

    _instance: ExtractionService | None = None

    def __init__(self):
        self.model = None
        self.tokenizer = None
        self.patients: dict[str, dict] = {}
        self._policy_caches: dict[str, PolicyCacheEntry] = {}
        self._default_policy: str = POLICY
        self._sampler = None
        self._logits_processors = None
        self.initialized = False
        self._backend = "mlx"
        self._gpu_lock = asyncio.Lock()  # Prevent concurrent GPU usage

    @classmethod
    def get_instance(cls) -> ExtractionService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self):
        """Load model, cache prompt prefix for default policy, discover patients. Called once at startup."""
        if self.initialized:
            return

        # Discover patients
        self.patients = self._discover_patients()
        logger.info("  Discovered %d patients", len(self.patients))

        backend = os.environ.get("EXTRACTION_BACKEND") or "mlx"
        self._backend = backend

        if backend == "mlx":
            # Load model
            logger.info("ExtractionService: Loading MedGemma model (MLX)...")
            from mlx_lm import load
            from mlx_lm.sample_utils import make_sampler, make_logits_processors

            self.model, self.tokenizer = load(str(MODEL_ID))
            self._sampler = make_sampler(temp=0.1, top_p=0.9)
            self._logits_processors = make_logits_processors(repetition_penalty=1.1)
        else:
            logger.info("ExtractionService: Using %s backend (no local model loaded).", backend)

        # Load default policy (trees + KV cache)
        self._ensure_policy_loaded(self._default_policy)

        self.initialized = True
        logger.info("ExtractionService: Ready.")

    def _ensure_policy_loaded(self, slug: str) -> PolicyCacheEntry:
        """Load and cache a policy's trees + KV prefix. Returns cached entry if already loaded."""
        if slug in self._policy_caches:
            return self._policy_caches[slug]

        logger.info("ExtractionService: Loading policy '%s'...", slug)
        enriched_path, original_path = tree_paths(slug)

        tree = load_tree(enriched_path)
        leaf_nodes = get_all_criteria(tree)
        tree_original = load_tree(original_path)
        leaf_nodes_original = get_all_criteria(tree_original)

        all_criteria = [
            {
                "id": cid,
                "source_text": node.source_text or node.summary or node.name,
                "search_description": leaf_nodes_original[cid].search_description if cid in leaf_nodes_original else node.search_description,
                "keywords": node.keywords,
                "anti_keywords": node.anti_keywords,
                "negated": node.negated,
            }
            for cid, node in leaf_nodes.items()
        ]
        criteria = [c for c in all_criteria if not c.get("negated")]
        negated_criteria = [c for c in all_criteria if c.get("negated")]
        logger.info("  Loaded %d criteria (%d for model, %d negated)", len(all_criteria), len(criteria), len(negated_criteria))

        prefix_cache = None
        if self._backend == "mlx" and self.model is not None:
            from mlx_lm.models.cache import make_prompt_cache
            from mlx_lm.generate import generate_step
            import mlx.core as mx

            logger.info("  Caching KV prefix for '%s'...", slug)
            t_cache = time.time()
            prompt_prefix = _build_prompt_prefix(criteria)
            prefix_tokens = mx.array(self.tokenizer.encode(prompt_prefix))
            prefix_cache = make_prompt_cache(self.model)
            for _ in generate_step(prefix_tokens, self.model, max_tokens=0, prompt_cache=prefix_cache):
                pass
            mx.eval(*[kv.state for kv in prefix_cache])
            logger.info("  Prefix cached: %d tokens (%.1fs)", prefix_cache[0].offset, time.time() - t_cache)

        entry = PolicyCacheEntry(
            slug=slug,
            tree=tree,
            tree_original=tree_original,
            all_criteria=all_criteria,
            criteria=criteria,
            negated_criteria=negated_criteria,
            prefix_cache=prefix_cache,
        )
        self._policy_caches[slug] = entry
        return entry

    def _discover_patients(self) -> dict[str, dict]:
        """Discover patients from new notes/ structure or legacy soap_notes/."""
        import json as _json
        patients: dict[str, dict] = {}

        # --- New structure: notes/{uuid}/metadata.json ---
        if NOTES_ROOT_NEW.exists():
            for metadata_file in sorted(NOTES_ROOT_NEW.glob("*/metadata.json")):
                try:
                    meta = _json.loads(metadata_file.read_text(encoding="utf-8"))
                    uuid = meta.get("uuid", "")
                    name = meta.get("patient_name", "")
                    if not uuid or not name:
                        continue
                    note_dir = metadata_file.parent
                    notes_meta = meta.get("notes", [])
                    note_files = []  # list of (filename, text)
                    for nm in notes_meta:
                        fn = nm.get("filename", "")
                        note_path = note_dir / fn
                        if note_path.exists():
                            note_files.append({
                                "filename": fn,
                                "title": nm.get("title", fn),
                                "type": nm.get("type", "soap"),
                                "text": note_path.read_text(encoding="utf-8"),
                            })
                    patients[uuid] = {
                        "name": name,
                        "uuid": uuid,
                        "policy": meta.get("policy", ""),
                        "files": [n["filename"] for n in note_files],
                        "note_files": note_files,  # list of {filename, title, type, text}
                        # legacy "text" field: concatenation for backward compat
                        "text": "\n\n---\n\n".join(n["text"] for n in note_files),
                    }
                except (Exception,):
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
                note_text = txt_file.read_text(encoding="utf-8")
                filename = str(txt_file.relative_to(NOTES_ROOT))
                patients[uuid] = {
                    "name": name,
                    "uuid": uuid,
                    "policy": "rheumatoid_arthritis_initial_auth",
                    "files": [filename],
                    "note_files": [{"filename": filename, "title": "SOAP Note", "type": "soap", "text": note_text}],
                    "text": note_text,
                }

        return patients

    def _run_inference(self, suffix: str, policy_entry: PolicyCacheEntry, max_tokens: int = 8000) -> str:
        if self._backend != "mlx":
            full_prompt = _build_prompt_prefix(policy_entry.criteria) + suffix
            # Strip Gemma chat format tokens — Gemini takes plain text
            for token in ("<start_of_turn>user\n", "<end_of_turn>\n", "<start_of_turn>model\n"):
                full_prompt = full_prompt.replace(token, "")
            return self._run_inference_gemini(full_prompt, max_tokens)

        import gc
        import mlx.core as mx
        from mlx_lm import generate

        cache = copy.deepcopy(policy_entry.prefix_cache)
        # Ensure all cached KV state is fully materialized on GPU before use
        mx.eval(*[kv.state for kv in cache])
        suffix_tokens = self.tokenizer.encode(suffix, add_special_tokens=False)
        output = generate(
            self.model, self.tokenizer, prompt=suffix_tokens, max_tokens=max_tokens,
            verbose=False, prompt_cache=cache,
            sampler=self._sampler, logits_processors=self._logits_processors,
        )
        # Flush pending GPU computations, free per-note cache, clear Metal buffer pool
        mx.eval()
        del cache
        gc.collect()
        mx.clear_cache()
        return output.strip()

    def _run_inference_full(self, prompt: str, max_tokens: int = 8000) -> str:
        if getattr(self, "_backend", "mlx") != "mlx":
            for token in ("<start_of_turn>user\n", "<end_of_turn>\n", "<start_of_turn>model\n"):
                prompt = prompt.replace(token, "")
            return self._run_inference_gemini(prompt, max_tokens)

        from mlx_lm import generate

        output = generate(
            self.model, self.tokenizer, prompt=prompt, max_tokens=max_tokens,
            verbose=False, sampler=self._sampler, logits_processors=self._logits_processors,
        )
        return output.strip()

    def _run_inference_gemini(self, prompt: str, max_tokens: int = 8000) -> str:
        from google import genai

        api_key = os.environ.get("LANGEXTRACT_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("LANGEXTRACT_API_KEY or GOOGLE_API_KEY env var required for Gemini backend")

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.05,
                top_p=0.9,
                max_output_tokens=max_tokens,
                thinking_config=genai.types.ThinkingConfig(thinking_budget=0),
            ),
        )
        result = response.text.strip()
        if not result:
            logger.warning(
                "Gemini returned empty response. Finish reason: %s",
                response.candidates[0].finish_reason if response.candidates else "NO_CANDIDATES",
            )
        return result

    def _merge_note_results(
        self,
        per_note_results: list[tuple[str, list[dict]]],
        combined_text: str,
    ) -> dict[str, dict]:
        """Delegate to module-level merge_note_results()."""
        return merge_note_results(per_note_results, combined_text)

    def _resolve_policy(self, uuid: str, policy: str | None) -> str:
        """Resolve which policy slug to use: explicit param > patient metadata > default."""
        if policy:
            return policy
        pat = self.patients.get(uuid)
        if pat and pat.get("policy"):
            return pat["policy"]
        return self._default_policy

    def extract(self, uuid: str, policy: str | None = None) -> Generator[dict, None, None]:
        """Yield SSE events during per-note extraction.

        Event types:
        - {"type": "status", "data": {"message": "..."}}
        - {"type": "run", "data": {"run": N, "note": filename, "criteria": [...], "time_s": ...}}
        - {"type": "policy", "data": {...}}
        - {"type": "complete", "data": {"uuid": ..., "name": ..., ...}}
        - {"type": "error", "data": {"message": "..."}}
        """
        if not self.initialized:
            yield {"type": "error", "data": {"message": "Service not initialized. Model still loading."}}
            return

        # Resolve UUID prefix to full UUID
        resolved = uuid if uuid in self.patients else None
        if not resolved:
            matches = [u for u in self.patients if u.startswith(uuid)]
            if len(matches) == 1:
                resolved = matches[0]
        if not resolved:
            yield {"type": "error", "data": {"message": f"Patient {uuid} not found"}}
            return

        # Resolve and load policy (lazily if needed)
        slug = self._resolve_policy(resolved, policy)
        try:
            policy_entry = self._ensure_policy_loaded(slug)
        except Exception as e:
            yield {"type": "error", "data": {"message": f"Failed to load policy '{slug}': {e}"}}
            return

        pat = self.patients[resolved]
        note_files = pat.get("note_files", [])
        combined_text = pat.get("text", "")

        # If no per-note structure, fall back to single combined extraction
        if not note_files:
            note_files = [{"filename": pat["files"][0] if pat["files"] else "note.txt",
                           "title": "SOAP Note", "type": "soap", "text": combined_text}]

        total_notes = len(note_files)
        per_note_results: list[tuple[str, list[dict]]] = []
        total_inference_time = 0
        all_merged: dict[str, dict] = {}

        for note_idx, note_info in enumerate(note_files):
            filename = note_info["filename"]
            title = note_info.get("title", filename)
            note_text = note_info["text"]

            yield {"type": "status", "data": {
                "message": f"Extracting from {title} ({note_idx + 1}/{total_notes})..."
            }}

            suffix = _build_prompt_suffix(note_text)
            t0 = time.time()
            raw = self._run_inference(suffix, policy_entry, max_tokens=8000)
            run_time = time.time() - t0
            total_inference_time += run_time

            parsed = _parse_json_response(raw)
            if parsed is None:
                yield {"type": "status", "data": {"message": f"Note {note_idx + 1}: JSON repair in progress..."}}
                repair_prompt = (
                    "<start_of_turn>user\n"
                    "Convert the following clinical evidence analysis into a valid JSON array.\n"
                    "Each item: {criterion_id: string, met: boolean, evidence: string}.\n"
                    "Output ONLY the JSON array, nothing else.\n\n"
                    f"{raw}\n"
                    "<end_of_turn>\n<start_of_turn>model\n["
                )
                raw2 = "[" + self._run_inference_full(repair_prompt)
                parsed = _parse_json_response(raw2)

            if parsed:
                validated = validate_evidence(parsed, policy_entry.all_criteria, note_text)
                per_note_results.append((filename, validated))

                # Update running merge
                all_merged = self._merge_note_results(per_note_results, combined_text)

                yield {
                    "type": "run",
                    "data": {
                        "run": note_idx + 1,
                        "note": filename,
                        "criteria": [
                            {k: v for k, v in item.items() if v is not None and v != ""}
                            for item in all_merged.values()
                        ],
                        "time_s": round(run_time, 1),
                    },
                }
            else:
                per_note_results.append((filename, []))
                yield {"type": "status", "data": {"message": f"Note {note_idx + 1}: Failed to parse JSON"}}

        # Add negated criteria (auto-met, no source note needed)
        filtered = list(all_merged.values())
        for nc in policy_entry.negated_criteria:
            if nc["id"] not in all_merged:
                filtered.append({"criterion_id": nc["id"], "met": True, "evidence": "No mention found"})

        # Evaluate policy tree
        results: dict[str, CriterionResult] = {}
        for item in filtered:
            cid = item["criterion_id"]
            results[cid] = CriterionResult(
                criterion_id=cid, met=True, evidence=item.get("evidence", ""),
                source_ref=item.get("source_note") or (pat["files"][0] if pat["files"] else ""),
            )

        status = get_status(policy_entry.tree, results)

        yield {
            "type": "policy",
            "data": {
                "overall": status.overall,
                "met_count": status.met_count,
                "total_count": status.total_count,
                "pending_count": status.pending_count,
                "criteria": status.criteria,
            },
        }

        yield {
            "type": "complete",
            "data": {
                "uuid": uuid,
                "name": pat["name"],
                "eligible": status.overall,
                "met_criteria": [
                    {k: v for k, v in item.items() if v is not None and v != ""}
                    for item in filtered
                ],
                "met_count": status.met_count,
                "total_count": status.total_count,
                "inference_time_s": round(total_inference_time, 1),
            },
        }
