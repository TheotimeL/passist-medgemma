"""Drug field parser using MedGemma 4B.

Extracts structured drug fields (name, strength, route, frequency, dates,
is_prior_therapy, failure_reason) from raw clinical evidence text.

The extraction model focuses on finding evidence; this small 4B model
handles the structured field extraction as a fast post-processing step.

Loaded lazily on first use — does NOT block server startup.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_MODEL_ID = "mlx-community/medgemma-1.5-4b-it-bf16"


def _get_evidence_text(entry: dict) -> str:
    """Return the best available evidence text from an extraction entry.

    Prefers joined evidence_snippets (richer context), falls back to evidence.
    """
    snippets = entry.get("evidence_snippets")
    if snippets and isinstance(snippets, list) and len(snippets) > 0:
        return "\n".join(snippets)
    return entry.get("evidence") or ""


_PRESCRIBER_PROMPT_TEMPLATE = (
    "<start_of_turn>user\n"
    "Extract prescriber information from each clinical evidence entry below.\n\n"
    "For each entry, extract:\n"
    "- prescriber_name: the provider/doctor name (e.g. \"Dr. Jane Smith, MD\")\n"
    "- prescriber_specialty: their medical specialty (e.g. \"Rheumatology\", \"Gastroenterology\")\n\n"
    "IMPORTANT: Look for provider names in signature blocks, \"Provider:\" lines, or \"Dr.\" mentions.\n"
    "If a field is not mentioned, use empty string.\n\n"
    "Example 1:\n"
    "Entry: \"Provider: Dr. Robert Chen, MD, Department of Rheumatology, Verilex Medical Group\"\n"
    "Output: {{\"prescriber_name\": \"Dr. Robert Chen, MD\", \"prescriber_specialty\": \"Rheumatology\"}}\n\n"
    "Example 2:\n"
    "Entry: \"Electronically signed, Dr. Sarah Kim, MD\\nInternal Medicine - Specialist Care\\nMetroplex Health System\"\n"
    "Output: {{\"prescriber_name\": \"Dr. Sarah Kim, MD\", \"prescriber_specialty\": \"Internal Medicine\"}}\n\n"
    "Look for specialty in: \"Department of [X]\" lines, signature blocks under provider name, "
    "\"[X] Clinic\" or \"[X] Center\" in facility, credential suffixes. "
    "Extract the most specific specialty mentioned.\n\n"
    "Entries:\n"
    "{entries_text}\n\n"
    "Output ONLY a JSON array with one object per entry, nothing else.\n"
    "<end_of_turn>\n"
    "<start_of_turn>model\n["
)

_PROMPT_TEMPLATE = (
    "<start_of_turn>user\n"
    "Extract structured drug information from each clinical evidence entry below.\n\n"
    "For each entry, extract:\n"
    "- entry_index: the entry number (1-based)\n"
    "- drug_name: the medication name mentioned in the text\n"
    "- drug_strength: dosage amount only (e.g. \"20 mg\", \"40 mg\")\n"
    "- drug_route: route of administration (e.g. \"subcutaneous\", \"oral\", \"IV\")\n"
    "- drug_frequency: how often taken (e.g. \"every 2 weeks\", \"weekly\", \"BID\")\n"
    "- drug_dates: therapy start/stop dates if mentioned\n"
    "- is_prior_therapy: true if the drug was previously tried/failed/discontinued, false if current or newly requested\n"
    "- failure_reason: reason for discontinuation if mentioned\n"
    "- source_text: the EXACT sentence or phrase from the entry that mentions this drug (copy verbatim)\n\n"
    "is_prior_therapy rules:\n"
    "- true: \"stopped\", \"discontinued\", \"failed\", \"prior\", \"previous\", past-tense dates\n"
    "- false: \"current\", \"active\", \"continue\", \"ongoing\", or newly prescribed in Plan\n"
    "- false: listed under \"Current Medications\" with status \"current\"/\"active\"\n"
    "- true: listed with a stop reason or \"STOPPED\" status\n"
    "- When unclear, default to true (doctor can override)\n\n"
    "IMPORTANT: If an entry mentions MULTIPLE drugs, output a SEPARATE object for each drug. "
    "Include entry_index (the entry number) in each object so results can be mapped back.\n\n"
    "IMPORTANT: Always extract the drug name if ANY medication is mentioned in the text. "
    "If a field is not mentioned, use empty string. Only set drug_name to empty string if truly no medication is named.\n\n"
    "Example 1 (single drug):\n"
    "Entry 1: \"Patient has a chronic condition. DrugA 10 mg oral daily from Jan 2020 to Mar 2020, discontinued due to side effects\"\n"
    "Output: [{{\"entry_index\": 1, \"drug_name\": \"DrugA\", \"drug_strength\": \"10 mg\", \"drug_route\": \"oral\", "
    "\"drug_frequency\": \"daily\", \"drug_dates\": \"Jan 2020 - Mar 2020\", "
    "\"is_prior_therapy\": true, \"failure_reason\": \"side effects\", "
    "\"source_text\": \"DrugA 10 mg oral daily from Jan 2020 to Mar 2020, discontinued due to side effects\"}}]\n\n"
    "Example 2 (multi-drug):\n"
    "Entry 2: \"DrugB 15 mg weekly: started 2019, STOPPED (hepatotoxicity). DrugC 500 mg BID: current\"\n"
    "Output: [{{\"entry_index\": 2, \"drug_name\": \"DrugB\", \"drug_strength\": \"15 mg\", \"drug_route\": \"\", "
    "\"drug_frequency\": \"weekly\", \"drug_dates\": \"started 2019\", "
    "\"is_prior_therapy\": true, \"failure_reason\": \"hepatotoxicity\", "
    "\"source_text\": \"DrugB 15 mg weekly: started 2019, STOPPED (hepatotoxicity)\"}}, "
    "{{\"entry_index\": 2, \"drug_name\": \"DrugC\", \"drug_strength\": \"500 mg\", \"drug_route\": \"\", "
    "\"drug_frequency\": \"BID\", \"drug_dates\": \"\", "
    "\"is_prior_therapy\": false, \"failure_reason\": \"\", "
    "\"source_text\": \"DrugC 500 mg BID: current\"}}]\n\n"
    "Entries:\n"
    "{entries_text}\n\n"
    "Output ONLY a JSON array with one object per entry, nothing else.\n"
    "<end_of_turn>\n"
    "<start_of_turn>model\n["
)


class DrugFieldParser:
    """Singleton that loads MedGemma 4B lazily for drug field parsing."""

    _instance: DrugFieldParser | None = None

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._ready = False

    @classmethod
    def get_instance(cls) -> DrugFieldParser:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _ensure_loaded(self):
        if self._ready:
            return

        backend = os.environ.get("EXTRACTION_BACKEND") or "mlx"
        self._backend = backend

        if backend == "mlx":
            logger.info("DrugFieldParser: Loading %s ...", _MODEL_ID)
            from mlx_lm import load
            self._model, self._tokenizer = load(str(_MODEL_ID))
            logger.info("DrugFieldParser: Ready.")
        elif backend == "ollama":
            logger.info("DrugFieldParser: Using Ollama backend (model managed by Ollama server).")
        else:
            logger.info("DrugFieldParser: Using %s backend.", backend)

        self._ready = True

    def parse(self, entries: list[dict]) -> list[dict]:
        """Extract structured drug fields from evidence text.

        Each entry should have 'evidence' (and optionally 'criterion_id').
        Returns a list with drug fields added. May be longer than input if
        multi-drug entries are split into separate results.
        """
        if not entries:
            return entries

        self._ensure_loaded()

        logger.info("Parsing %d entries", len(entries))

        # Build prompt with the evidence texts
        lines = []
        for idx, entry in enumerate(entries):
            evidence = _get_evidence_text(entry)
            lines.append(f"{idx + 1}. {evidence}")
        entries_text = "\n".join(lines)
        prompt = _PROMPT_TEMPLATE.format(entries_text=entries_text)

        # Run inference (model is primed with "[" so we prepend it)
        raw = self._run_inference(prompt, max_tokens=2048)
        if self._backend == "mlx":
            raw = "[" + raw
        logger.debug("Raw output (first 500 chars): %s", raw[:500])
        parsed = self._parse_json(raw)
        if parsed is None:
            logger.warning("[DrugFieldParser] _parse_json returned None. Raw output (first 800 chars): %s", raw[:800])
        logger.info("Parsed %s items from %d entries", len(parsed) if parsed else "None", len(entries))

        if parsed:
            _DRUG_FIELDS = (
                "drug_name", "drug_strength", "drug_route", "drug_frequency",
                "drug_dates", "is_prior_therapy", "failure_reason", "source_text",
            )

            # Group parsed results by entry_index (1-based)
            by_entry: dict[int, list[dict]] = {}
            for result in parsed:
                idx = result.get("entry_index")
                if idx is not None and isinstance(idx, int):
                    by_entry.setdefault(idx, []).append(result)
                else:
                    # Fallback: no entry_index, assign sequentially
                    by_entry.setdefault(-1, []).append(result)

            # If no entry_index fields at all, fall back to old 1:1 zip behavior
            if list(by_entry.keys()) == [-1]:
                logger.debug("No entry_index in output, falling back to 1:1 mapping")
                if len(parsed) < len(entries):
                    logger.warning("Partial: got %d/%d entries (truncated)", len(parsed), len(entries))
                for entry, result in zip(entries, parsed):
                    for field in _DRUG_FIELDS:
                        val = result.get(field)
                        if val is not None and val != "":
                            entry[field] = val
            else:
                # Map results back using entry_index
                extra_entries: list[dict] = []
                for entry_idx_0based, entry in enumerate(entries):
                    entry_idx_1based = entry_idx_0based + 1
                    results_for_entry = by_entry.get(entry_idx_1based, [])
                    if not results_for_entry:
                        continue

                    # First drug maps onto the original entry
                    first_result = results_for_entry[0]
                    for field in _DRUG_FIELDS:
                        val = first_result.get(field)
                        if val is not None and val != "":
                            entry[field] = val

                    # Additional drugs create new entry dicts
                    for extra_result in results_for_entry[1:]:
                        new_entry = {
                            "criterion_id": entry.get("criterion_id"),
                            "evidence": entry.get("evidence"),
                            "evidence_snippets": entry.get("evidence_snippets"),
                            "source_note": entry.get("source_note"),
                            "met": entry.get("met", True),
                        }
                        for field in _DRUG_FIELDS:
                            val = extra_result.get(field)
                            if val is not None and val != "":
                                new_entry[field] = val
                        extra_entries.append(new_entry)
                        logger.info("Multi-drug split: entry %d extra drug '%s'",
                                    entry_idx_1based, extra_result.get("drug_name", "?"))

                if extra_entries:
                    logger.info("Adding %d extra entries from multi-drug parsing", len(extra_entries))
                    entries.extend(extra_entries)

            for entry in entries:
                logger.debug("%s → name=%s, strength=%s, prior=%s",
                             entry.get('criterion_id', '?')[:40],
                             entry.get('drug_name', ''),
                             entry.get('drug_strength', ''),
                             entry.get('is_prior_therapy', ''))
        else:
            logger.warning("Failed to parse any results")

        return entries

    def parse_prescriber(self, entries: list[dict]) -> list[dict]:
        """Extract prescriber_name and prescriber_specialty from evidence text.

        Each entry should have 'evidence' (and optionally 'criterion_id').
        Returns the same list with prescriber fields added.
        """
        if not entries:
            return entries

        self._ensure_loaded()

        logger.info("Parsing prescriber from %d entries", len(entries))

        lines = []
        for idx, entry in enumerate(entries):
            evidence = _get_evidence_text(entry)
            lines.append(f"{idx + 1}. {evidence}")
        entries_text = "\n".join(lines)
        prompt = _PRESCRIBER_PROMPT_TEMPLATE.format(entries_text=entries_text)

        raw = self._run_inference(prompt, max_tokens=512)
        if self._backend == "mlx":
            raw = "[" + raw
        logger.debug("Prescriber raw output (first 500 chars): %s", raw[:500])
        parsed = self._parse_json(raw)
        logger.info("Prescriber parsed %s entries", len(parsed) if parsed else "None")

        if parsed:
            _PRESCRIBER_FIELDS = ("prescriber_name", "prescriber_specialty")
            for entry, result in zip(entries, parsed):
                for field in _PRESCRIBER_FIELDS:
                    val = result.get(field)
                    if val is not None and val != "":
                        entry[field] = val
                logger.debug("Prescriber %s → name=%s, specialty=%s",
                             entry.get('criterion_id', '?')[:40],
                             entry.get('prescriber_name', ''),
                             entry.get('prescriber_specialty', ''))
        else:
            logger.warning("Failed to parse prescriber results")

        return entries

    def _run_inference(self, prompt: str, max_tokens: int = 1024) -> str:
        if getattr(self, "_backend", "mlx") != "mlx":
            if self._backend == "ollama":
                return self._run_inference_ollama(prompt, max_tokens)
            return self._run_inference_gemini(prompt, max_tokens)

        import gc
        import mlx.core as mx
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        # Clear any lingering Metal state from the extraction model
        mx.eval()
        mx.clear_cache()

        sampler = make_sampler(temp=0.1)
        output = generate(
            self._model, self._tokenizer,
            prompt=prompt, max_tokens=max_tokens,
            verbose=False, sampler=sampler,
        )

        # Clean up after inference
        mx.eval()
        gc.collect()
        mx.clear_cache()

        return output.strip()

    def _run_inference_ollama(self, prompt: str, max_tokens: int = 2048) -> str:
        # Ollama serves models in GGUF format (quantized for efficient GPU inference)
        import requests as _requests
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        response = _requests.post(f"{host}/api/generate", json={
            "model": "MedAIBase/MedGemma1.5",
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": max_tokens},
        })
        response.raise_for_status()
        return response.json()["response"].strip()

    def _run_inference_gemini(self, prompt: str, max_tokens: int = 2048) -> str:
        from google import genai

        api_key = os.environ.get("LANGEXTRACT_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("LANGEXTRACT_API_KEY or GOOGLE_API_KEY required for Gemini backend")

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai.types.GenerateContentConfig(temperature=0.0, max_output_tokens=max_tokens),
        )
        return response.text.strip()

    @staticmethod
    def _parse_json(raw: str) -> list[dict] | None:
        import re
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\n?```\s*$", "", raw, flags=re.MULTILINE)
        raw = raw.strip()
        try:
            result = json.loads(raw)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None
