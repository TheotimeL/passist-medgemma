"""Drug field parser using MedGemma 4B.

Extracts structured drug fields (name, strength, route, frequency, dates,
is_prior_therapy, failure_reason) from raw clinical evidence text.

The 27B extraction model focuses on finding evidence; this small 4B model
handles the structured field extraction as a fast post-processing step.

Loaded lazily on first use — does NOT block server startup.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_MODEL_ID = "mlx-community/medgemma-1.5-4b-it-bf16"

_PROMPT_TEMPLATE = (
    "<start_of_turn>user\n"
    "Extract structured drug information from each clinical evidence entry below.\n\n"
    "For each entry, extract:\n"
    "- drug_name: the medication name mentioned in the text\n"
    "- drug_strength: dosage amount only (e.g. \"20 mg\", \"40 mg\")\n"
    "- drug_route: route of administration (e.g. \"subcutaneous\", \"oral\", \"IV\")\n"
    "- drug_frequency: how often taken (e.g. \"every 2 weeks\", \"weekly\", \"BID\")\n"
    "- drug_dates: therapy start/stop dates if mentioned\n"
    "- is_prior_therapy: true if the drug was previously tried/failed/discontinued, false if current or newly requested\n"
    "- failure_reason: reason for discontinuation if mentioned\n"
    "- source_text: the EXACT sentence or phrase from the entry that mentions this drug (copy verbatim)\n\n"
    "IMPORTANT: Always extract the drug name if ANY medication is mentioned in the text. "
    "If a field is not mentioned, use empty string. Only set drug_name to empty string if truly no medication is named.\n\n"
    "Example:\n"
    "Entry: \"Patient has RA. DrugA 10 mg oral daily from Jan 2020 to Mar 2020, discontinued due to side effects\"\n"
    "Output: {{\"drug_name\": \"DrugA\", \"drug_strength\": \"10 mg\", \"drug_route\": \"oral\", "
    "\"drug_frequency\": \"daily\", \"drug_dates\": \"Jan 2020 - Mar 2020\", "
    "\"is_prior_therapy\": true, \"failure_reason\": \"side effects\", "
    "\"source_text\": \"DrugA 10 mg oral daily from Jan 2020 to Mar 2020, discontinued due to side effects\"}}\n\n"
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
        else:
            logger.info("DrugFieldParser: Using %s backend.", backend)

        self._ready = True

    def parse(self, entries: list[dict]) -> list[dict]:
        """Extract structured drug fields from evidence text.

        Each entry should have 'evidence' (and optionally 'criterion_id').
        Returns the same list with drug fields added.
        """
        if not entries:
            return entries

        self._ensure_loaded()

        print(f"[DrugFieldParser] Parsing {len(entries)} entries")

        # Build prompt with the evidence texts
        lines = []
        for idx, entry in enumerate(entries):
            evidence = entry.get("evidence") or ""
            lines.append(f"{idx + 1}. {evidence}")
        entries_text = "\n".join(lines)
        prompt = _PROMPT_TEMPLATE.format(entries_text=entries_text)

        # Run inference (model is primed with "[" so we prepend it)
        raw = self._run_inference(prompt)
        if getattr(self, "_backend", "mlx") == "mlx":
            raw = "[" + raw
        print(f"[DrugFieldParser] Raw output (first 500 chars): {raw[:500]}")
        parsed = self._parse_json(raw)
        print(f"[DrugFieldParser] Parsed {len(parsed) if parsed else 'None'} entries")

        if parsed:
            _DRUG_FIELDS = (
                "drug_name", "drug_strength", "drug_route", "drug_frequency",
                "drug_dates", "is_prior_therapy", "failure_reason", "source_text",
            )
            if len(parsed) < len(entries):
                print(f"[DrugFieldParser] Partial: got {len(parsed)}/{len(entries)} entries (truncated)")
            for entry, result in zip(entries, parsed):
                for field in _DRUG_FIELDS:
                    val = result.get(field)
                    if val is not None and val != "":
                        entry[field] = val
                print(f"[DrugFieldParser] {entry.get('criterion_id', '?')[:40]} → name={entry.get('drug_name', '')}, strength={entry.get('drug_strength', '')}, prior={entry.get('is_prior_therapy', '')}")
        else:
            print(f"[DrugFieldParser] Failed to parse any results")

        return entries

    def _run_inference(self, prompt: str) -> str:
        if getattr(self, "_backend", "mlx") != "mlx":
            return self._run_inference_gemini(prompt)

        import gc
        import mlx.core as mx
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        # Clear any lingering Metal state from the 27B model
        mx.eval()
        mx.clear_cache()

        sampler = make_sampler(temp=0.0)
        output = generate(
            self._model, self._tokenizer,
            prompt=prompt, max_tokens=1024,
            verbose=False, sampler=sampler,
        )

        # Clean up after inference
        mx.eval()
        gc.collect()
        mx.clear_cache()

        return output.strip()

    def _run_inference_gemini(self, prompt: str) -> str:
        from google import genai

        api_key = os.environ.get("LANGEXTRACT_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("LANGEXTRACT_API_KEY or GOOGLE_API_KEY required for Gemini backend")

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai.types.GenerateContentConfig(temperature=0.0, max_output_tokens=512),
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
