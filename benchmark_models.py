"""Benchmark: MedGemma 27B vs Meditron 7B vs LLaMA 3.1 8B for clinical criteria extraction.

Runs all 3 models against patient SOAP notes, compares against manually-curated
ground truth, and produces precision/recall/F1 + performance metrics.

Reuses all extraction logic (prompt building, JSON parsing, evidence validation)
from extraction_service.py — the canonical source for the extraction pipeline.

Usage:
    python benchmark_models.py                          # Full benchmark (all models, all patients)
    python benchmark_models.py --models MedGemma-27B    # Single model
    python benchmark_models.py --patients vanesa         # Specific patient(s)
"""

import argparse
import gc
import json
import os
import sys
import time
import traceback
from abc import ABC, abstractmethod

if "HF_TOKEN" not in os.environ:
    from dotenv import load_dotenv
    load_dotenv()
    if "HF_TOKEN" not in os.environ:
        raise SystemExit("ERROR: HF_TOKEN environment variable is required. Set it in .env or export it.")


sys.path.insert(0, ".")
from config import TREE_PATH, TREE_PATH_ORIGINAL, UUID_PATTERN, NOTES_ROOT, NOTES_ROOT_NEW, BENCHMARK_NOTES_ROOT
from policy_tree import load_tree, get_all_criteria
from extraction_service import (
    EXAMPLE_NOTE,
    _build_example_output,
    _build_criteria_block,
    _parse_json_response,
    validate_evidence,
    merge_note_results,
)


# ── Named shortcuts for CLI ──────────────────────────────────────────────────

# Default shortcuts (original 12 patients). Augmented dynamically from ground truth.
ALL_TEST_UUIDS = {
    "vanesa":     "520e6e72-a964-eaf5-8495-b41c77eff668",
    "broderick":  "2d701350-9a03-786d-661e-5cb4971efc20",
    "aide":       "5c9df1d3-2a63-1c68-b5fa-5ab79be51dad",
    "caridad":    "92181936-5f66-8913-309f-7eb45e5fc388",
    "alycia":     "aa20b461-6893-f4f8-cfce-4399c8065fe6",
    "analisa":    "affb0758-feb8-4754-2fc9-15b54d6b398b",
    "antonio":    "1a691f1f-6957-0fd7-cb3a-c0cc61c93371",
    "abram":      "8b8f1e13-b68f-5d5f-4230-0ac867267002",
    "dmitri":     "f7581664-09d0-495d-9585-5ad6e63e8ac5",
    "elena":      "17d5d247-d06e-4448-883d-eb451e7aad7a",
    "marcus":     "129412a7-6444-48b0-8236-ba915a86887d",
    "patricia":   "d840c23b-1a73-4045-9e92-49cf10be1e58",
}


# Pre-determined splits for reproducible benchmark runs (47 patients, alphabetical order)
# Run 1: 16 patients, Run 2: 16 patients, Run 3: 15 patients
BENCHMARK_RUNS = {
    1: [
        "804878af-4305-ec23-e58e-0b5af8886ced",  # abraham
        "ad98c861-9e9d-7989-7f34-84364b210b6b",  # alfred
        "47c71a0f-677d-93ce-0b2e-55618b84a1f6",  # ali
        "9f460bd6-ff04-a34a-d801-6194d079680f",  # allena
        "e2ce8653-1ae7-a68f-edbb-f14cff13d631",  # asuncion
        "a8abd48d-27e6-3cfd-ba8f-b217e74f560b",  # austin
        "1d6b070d-3ee0-8a14-9198-baef08bab6dd",  # ben
        "5d92c83d-bd17-0560-6f37-85879287d86b",  # bree
        "b46c5a06-f1f8-3b61-786f-0e7661e77b1a",  # chi
        "c7011fdd-57c1-9002-ddbf-936bd334ec18",  # cornell
        "d0b7ba26-c9e5-0667-6ac3-ebb0ab822afe",  # elfreda
        "353d8963-8b5b-6893-9b75-fc0d1f41edc3",  # emilio
        "2c88a607-cdb1-08b4-65c2-24d5d0f94865",  # ernesto
        "db32901b-9357-6701-a5bd-e3ed78e8b38e",  # estell
        "38441f2b-480d-9557-ec78-349bdb2f7d58",  # gabriel
        "95eb2215-065e-ce93-8a75-fc5e32d24cdb",  # gabriel
    ],
    2: [
        "16790570-f435-8a6d-f776-98aad23d0540",  # gilbert
        "f5954eda-abd8-8f1c-db56-c91de133574e",  # hanna
        "bfddb1d9-b134-95d3-15ef-c3e4e153b1eb",  # hedwig
        "24ab737e-3abd-07c8-980d-ca3f11da38fd",  # israel
        "c438a021-cb69-23f6-dc9b-159a53257af3",  # jacquelyne
        "c106918a-8cbc-3fd3-0a11-64c313392dd6",  # kirk
        "a7cb65d3-1827-f164-2320-57a8a8639b71",  # lashonda
        "0e057df9-1e06-5f89-73b5-868865e1b7b9",  # launa
        "f5466944-023c-8335-d534-53c5bb358b5d",  # laurinda
        "2ae5c0e3-31d6-cafb-b599-678f4345290f",  # lazaro
        "33ac74a3-81eb-f49e-0711-7e9af2a2c6ca",  # lelia
        "081daa4c-a3b5-9b16-7ceb-981d9bb398df",  # lourdes
        "1b4ec79a-b15a-8ee4-e713-82d40058f6b4",  # magdalene
        "79814692-3a13-453c-9dd5-cf5c33f3853a",  # myrle
        "d6d1e2ab-5584-a9f3-8451-57b84c00f41a",  # na
        "8c018069-c68e-e70c-e65c-ff4553295afb",  # nathaniel
    ],
    3: [
        "a0916e70-223c-eb74-551e-691f136a4dd1",  # nelson
        "515da9dd-0bf3-91c7-e7f7-17218b214352",  # noriko
        "44814255-0041-e565-c8bb-ba7c6a992536",  # octavio
        "d493060f-d99e-9276-a4a4-642730ab4308",  # pete
        "39420291-6aeb-b069-9522-76c7f067a60d",  # rashad
        "03b34d3f-bc3c-e043-3761-3bdb6e28e2c9",  # refugia
        "9e8cf1bf-367b-4556-1041-a6f33e65e070",  # richie
        "45954e28-5b6e-2f3e-21b0-a9bd623a8da6",  # rigoberto
        "7322949c-eefe-1e80-5f09-fd1e3d37739d",  # rosella
        "6121c5c8-46e5-f162-32e7-33947fdd59a4",  # sammie
        "63f6b38f-eb66-3305-f0b8-f7d8c632059a",  # sheryl
        "871ce1cb-35d6-09b8-af86-d91a78b1819f",  # stuart
        "5caa1e5b-d3d7-34ba-184b-ca6884c9d4ac",  # trent
        "da4efed5-d95a-48fc-5509-bed5d0675968",  # verena
        "22c8179d-81f5-d082-da0b-cd96486a86a0",  # vicenta
    ],
}


def _augment_uuids_from_ground_truth(gt_path: str) -> None:
    """Add short_name → UUID mappings from a ground truth file into ALL_TEST_UUIDS."""
    try:
        with open(gt_path) as f:
            gt = json.load(f)
        for uuid, info in gt.get("patients", {}).items():
            short = info.get("short_name", "").lower()
            if short and short not in ALL_TEST_UUIDS:
                ALL_TEST_UUIDS[short] = uuid
    except (FileNotFoundError, json.JSONDecodeError):
        pass


# ── Data loading (reuses config.py constants) ────────────────────────────────

def load_patients() -> dict:
    """Discover and load all patients from notes/ and soap_notes/."""
    patients: dict[str, dict] = {}

    # New structure: notes/{uuid}/metadata.json
    if NOTES_ROOT_NEW.exists():
        for metadata_file in sorted(NOTES_ROOT_NEW.glob("*/metadata.json")):
            try:
                meta = json.loads(metadata_file.read_text(encoding="utf-8"))
                uuid = meta.get("uuid", "")
                name = meta.get("patient_name", "")
                if not uuid or not name:
                    continue
                note_dir = metadata_file.parent
                notes_meta = meta.get("notes", [])
                note_files = []
                for nm in notes_meta:
                    fn = nm.get("filename", "")
                    note_path = note_dir / fn
                    if note_path.exists():
                        note_files.append({
                            "filename": fn,
                            "text": note_path.read_text(encoding="utf-8"),
                        })
                patients[uuid] = {
                    "name": name,
                    "uuid": uuid,
                    "files": [n["filename"] for n in note_files],
                    "note_files": note_files,
                    "text": "\n\n---\n\n".join(n["text"] for n in note_files),
                }
            except Exception:
                continue

    # Legacy fallback: soap_notes/*.txt
    if NOTES_ROOT.exists():
        for txt_file in sorted(NOTES_ROOT.glob("*.txt")):
            m = UUID_PATTERN.match(txt_file.name)
            if not m:
                continue
            uuid = m.group("uuid")
            if uuid in patients:
                continue
            patient_name = m.group("name").replace("_", " ")
            rel_path = str(txt_file.relative_to(NOTES_ROOT))
            if uuid not in patients:
                patients[uuid] = {"name": patient_name, "uuid": uuid, "files": [], "texts": []}
            patients[uuid]["files"].append(rel_path)
            patients[uuid].setdefault("texts", []).append(txt_file.read_text(encoding="utf-8"))

    # Benchmark notes: benchmark_soap_notes/*.txt (same naming convention as soap_notes/)
    if BENCHMARK_NOTES_ROOT.exists():
        for txt_file in sorted(BENCHMARK_NOTES_ROOT.glob("*.txt")):
            m = UUID_PATTERN.match(txt_file.name)
            if not m:
                continue
            uuid = m.group("uuid")
            if uuid in patients:
                continue
            patient_name = m.group("name").replace("_", " ")
            rel_path = str(txt_file.relative_to(BENCHMARK_NOTES_ROOT))
            patients[uuid] = {"name": patient_name, "uuid": uuid, "files": [rel_path], "texts": []}
            patients[uuid]["texts"].append(txt_file.read_text(encoding="utf-8"))

    # Finalize legacy patients
    for p in patients.values():
        if "texts" in p:
            p["text"] = "\n\n---\n\n".join(p["texts"])
            if "note_files" not in p:
                p["note_files"] = [{"filename": fn, "text": txt}
                                   for fn, txt in zip(p["files"], p["texts"])]
            del p["texts"]

    return patients


def load_criteria():
    """Load enriched + original trees and build criteria lists."""
    tree = load_tree(TREE_PATH)
    leaf_nodes = get_all_criteria(tree)
    tree_original = load_tree(TREE_PATH_ORIGINAL)
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
    return tree, all_criteria, criteria, negated_criteria


# ── Model backends ───────────────────────────────────────────────────────────

class ModelBackend(ABC):
    """Abstract backend for running LLM inference."""

    @abstractmethod
    def load(self) -> dict:
        """Load the model. Returns {"load_time_s": float, "peak_memory_mb": float}."""

    @abstractmethod
    def generate(self, prompt: str, max_tokens: int = 8000) -> tuple[str, float]:
        """Generate text from prompt. Returns (output_text, tokens_per_sec)."""

    @abstractmethod
    def unload(self) -> None:
        """Unload the model and free resources."""


class MLXBackend(ModelBackend):
    """Backend using mlx-lm for Apple Silicon GPU inference."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        self.model = None
        self.tokenizer = None

    def load(self) -> dict:
        import mlx.core as mx
        from mlx_lm import load
        from mlx_lm.sample_utils import make_sampler, make_logits_processors

        t0 = time.time()
        self.model, self.tokenizer = load(str(self.model_id))
        load_time = time.time() - t0

        self.sampler = make_sampler(temp=0.05, top_p=0.9)
        self.logits_processors = make_logits_processors(repetition_penalty=1.2)

        peak_mem = _get_metal_memory_mb()

        return {"load_time_s": round(load_time, 1), "peak_memory_mb": round(peak_mem, 0)}

    def generate(self, prompt: str, max_tokens: int = 8000) -> tuple[str, float]:
        from mlx_lm import generate

        t0 = time.time()
        output = generate(
            self.model, self.tokenizer,
            prompt=prompt, max_tokens=max_tokens, verbose=False,
            sampler=self.sampler, logits_processors=self.logits_processors,
        )
        elapsed = time.time() - t0

        output_tokens = len(self.tokenizer.encode(output))
        tok_per_sec = output_tokens / elapsed if elapsed > 0 else 0

        return output.strip(), tok_per_sec

    def unload(self) -> None:
        import mlx.core as mx
        del self.model
        del self.tokenizer
        self.model = None
        self.tokenizer = None
        gc.collect()
        mx.clear_cache()


class TransformersBackend(ModelBackend):
    """Backend using HuggingFace Transformers with MPS (Apple Silicon) or CPU."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        self.model = None
        self.tokenizer = None
        self.device = None

    def load(self) -> dict:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.device = "mps" if torch.backends.mps.is_available() else "cpu"

        t0 = time.time()
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id, trust_remote_code=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            torch_dtype=torch.float16 if self.device == "mps" else torch.float32,
            device_map=self.device,
            trust_remote_code=True,
        )
        load_time = time.time() - t0

        peak_mem = _get_metal_memory_mb()

        return {"load_time_s": round(load_time, 1), "peak_memory_mb": round(peak_mem, 0)}

    def generate(self, prompt: str, max_tokens: int = 8000) -> tuple[str, float]:
        import torch

        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
        input_len = inputs["input_ids"].shape[1]

        t0 = time.time()
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=0.05,
                top_p=0.9,
                repetition_penalty=1.2,
                do_sample=True,
            )
        elapsed = time.time() - t0

        output_tokens = outputs[0][input_len:]
        output_text = self.tokenizer.decode(output_tokens, skip_special_tokens=True)
        tok_per_sec = len(output_tokens) / elapsed if elapsed > 0 else 0

        return output_text.strip(), tok_per_sec

    def unload(self) -> None:
        import torch
        del self.model
        del self.tokenizer
        self.model = None
        self.tokenizer = None
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()


# ── Prompt formatters ────────────────────────────────────────────────────────

class PromptFormatter(ABC):
    """Abstract formatter that wraps prompt content into model-specific chat templates."""

    @abstractmethod
    def format_few_shot(
        self,
        system_content: str,
        example_user: str,
        example_assistant: str,
        real_user: str,
        assistant_prefix: str = "",
    ) -> str:
        """Format a few-shot prompt: system + example turn + real turn."""


class GemmaFormatter(PromptFormatter):
    """Gemma chat template used by MedGemma."""

    def format_few_shot(self, system_content: str, example_user: str, example_assistant: str,
                        real_user: str, assistant_prefix: str = "") -> str:
        prompt = (
            f"<start_of_turn>user\n{system_content}\n\n{example_user}\n<end_of_turn>\n"
            f"<start_of_turn>model\n{example_assistant}\n<end_of_turn>\n"
            f"<start_of_turn>user\n{real_user}\n<end_of_turn>\n"
            f"<start_of_turn>model\n"
        )
        if assistant_prefix:
            prompt += assistant_prefix
        return prompt


class LlamaFormatter(PromptFormatter):
    """LLaMA 3.1 chat template."""

    def format_few_shot(self, system_content: str, example_user: str, example_assistant: str,
                        real_user: str, assistant_prefix: str = "") -> str:
        prompt = (
            "<|begin_of_text|>"
            f"<|start_header_id|>system<|end_header_id|>\n\n{system_content}<|eot_id|>"
            f"<|start_header_id|>user<|end_header_id|>\n\n{example_user}<|eot_id|>"
            f"<|start_header_id|>assistant<|end_header_id|>\n\n{example_assistant}<|eot_id|>"
            f"<|start_header_id|>user<|end_header_id|>\n\n{real_user}<|eot_id|>"
            "<|start_header_id|>assistant<|end_header_id|>\n\n"
        )
        if assistant_prefix:
            prompt += assistant_prefix
        return prompt


class MeditronFormatter(PromptFormatter):
    """Meditron (Llama 2 based) chat template."""

    def format_few_shot(self, system_content: str, example_user: str, example_assistant: str,
                        real_user: str, assistant_prefix: str = "") -> str:
        prompt = (
            f"<s>[INST] <<SYS>>\n{system_content}\n<</SYS>>\n\n{example_user} [/INST] "
            f"{example_assistant} </s>"
            f"<s>[INST] {real_user} [/INST] "
        )
        if assistant_prefix:
            prompt += assistant_prefix
        return prompt


class MistralFormatter(PromptFormatter):
    """Mistral [INST] chat template (no system prompt support — system folded into first user turn)."""

    def format_few_shot(self, system_content: str, example_user: str, example_assistant: str,
                        real_user: str, assistant_prefix: str = "") -> str:
        prompt = (
            f"<s>[INST] {system_content}\n\n{example_user} [/INST]"
            f"{example_assistant}</s> "
            f"[INST] {real_user} [/INST]"
        )
        if assistant_prefix:
            prompt += assistant_prefix
        return prompt


# ── Model configurations ─────────────────────────────────────────────────────

MODEL_CONFIGS = {
    "MedGemma-27B": {
        "model_id": "mlx-community/medgemma-27b-text-it-bf16",
        "backend_cls": MLXBackend,
        "formatter_cls": GemmaFormatter,
    },
    "LLaMA-3.1-8B": {
        "model_id": "mlx-community/Meta-Llama-3.1-8B-Instruct-bf16",
        "backend_cls": MLXBackend,
        "formatter_cls": LlamaFormatter,
    },
    "BioMistral-7B": {
        "model_id": "models/biomistral-7b-dare-mlx",
        "backend_cls": MLXBackend,
        "formatter_cls": MistralFormatter,
    },
    # "Meditron-7B": {
    #     "model_id": "mlx-community/meditron-7b",
    #     "backend_cls": MLXBackend,
    #     "formatter_cls": MeditronFormatter,
    # },
}


# ── Utility ──────────────────────────────────────────────────────────────────

def _get_metal_memory_mb() -> float:
    """Get current Metal GPU memory usage in MB. Returns 0 if unavailable."""
    try:
        import mlx.core as mx
        return mx.get_peak_memory() / 1024 / 1024
    except Exception:
        pass
    try:
        import torch
        if torch.backends.mps.is_available():
            return torch.mps.current_allocated_memory() / 1024 / 1024
    except Exception:
        pass
    return 0.0


# ── Prompt building (model-agnostic content, model-specific wrapping) ────────

# System instructions: same content as extraction_service._build_prompt_prefix,
# but decomposed to allow model-specific chat template wrapping.
SYSTEM_INSTRUCTIONS = (
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
    "- prescriber_name (optional): provider name if this is a prescriber criterion\n"
    "- prescriber_specialty (optional): provider specialty, e.g. 'Rheumatology'\n\n"
    "RULES:\n"
    "1. Evidence must be EXACT text from the document. Do not paraphrase or add words.\n"
    "2. Each criterion needs its OWN evidence that specifically mentions what that criterion asks about.\n"
    "   Drug failure for Drug X requires evidence mentioning Drug X by name.\n"
    "3. For prescriber evidence: extract the provider line or signature block WITH name, department/specialty, and practice.\n"
    "4. If a criterion is not clearly supported, OMIT it. Most criteria will NOT be met."
)


def build_prompt(formatter: PromptFormatter, criteria: list[dict], note_text: str) -> str:
    """Build a complete prompt for a patient note using the given formatter.

    Uses _build_criteria_block and _build_example_output from extraction_service
    to ensure identical prompt content across benchmark and production.
    """
    criteria_block = _build_criteria_block(criteria)
    example_output = _build_example_output(criteria)

    system_and_criteria = f"{SYSTEM_INSTRUCTIONS}\n\nCRITERIA:\n{criteria_block}"

    example_user = f"DOCUMENT:\n{EXAMPLE_NOTE}"

    real_user = (
        "Now extract evidence from a COMPLETELY DIFFERENT patient document below.\n"
        "CRITICAL: Use ONLY text from THIS new document. The previous example is irrelevant.\n"
        "Do NOT copy any evidence from the example above — it is a different patient.\n\n"
        f"DOCUMENT:\n{note_text}"
    )

    return formatter.format_few_shot(
        system_content=system_and_criteria,
        example_user=example_user,
        example_assistant=example_output,
        real_user=real_user,
        assistant_prefix="REASONING:\n",
    )


# ── Extraction for a single patient ─────────────────────────────────────────

def extract_patient(
    backend: ModelBackend,
    formatter: PromptFormatter,
    criteria: list[dict],
    all_criteria: list[dict],
    negated_criteria: list[dict],
    patient: dict,
) -> dict:
    """Run per-note extraction for a single patient, matching production behavior.

    Extracts from each note individually, applies JSON repair on parse failure,
    then merges results using merge_note_results from extraction_service.
    """
    note_files = patient.get("note_files", [])
    combined_text = patient.get("text", "")

    # Fallback: if no per-note structure, treat concatenated text as single note
    if not note_files:
        note_files = [{"filename": patient["files"][0] if patient.get("files") else "note.txt",
                       "text": combined_text}]

    per_note_results: list[tuple[str, list[dict]]] = []
    total_time = 0
    total_tok_per_sec = []

    for note_idx, note_info in enumerate(note_files):
        filename = note_info["filename"]
        note_text = note_info["text"]

        print(f"    Note {note_idx + 1}/{len(note_files)} ({filename})...", end=" ", flush=True)
        prompt = build_prompt(formatter, criteria, note_text)

        t0 = time.time()
        try:
            raw, tok_s = backend.generate(prompt, max_tokens=8000)
        except Exception as e:
            print(f"ERROR: {e}")
            per_note_results.append((filename, []))
            continue
        elapsed = time.time() - t0
        total_time += elapsed
        total_tok_per_sec.append(tok_s)
        print(f"{elapsed:.1f}s ({tok_s:.1f} tok/s)")

        parsed = _parse_json_response(raw)

        # JSON repair: if parse failed, ask model to convert reasoning to JSON
        if parsed is None:
            print(f"      JSON repair in progress...", end=" ", flush=True)
            repair_prompt = formatter.format_few_shot(
                system_content=(
                    "Convert the following clinical evidence analysis into a valid JSON array.\n"
                    "Each item: {criterion_id: string, met: boolean, evidence: string}.\n"
                    "Output ONLY the JSON array, nothing else."
                ),
                example_user="",
                example_assistant="",
                real_user=raw,
                assistant_prefix="[",
            )
            try:
                raw2, _ = backend.generate(repair_prompt, max_tokens=4000)
                parsed = _parse_json_response("[" + raw2)
                if parsed:
                    print("OK")
                else:
                    print("FAILED")
            except Exception as e:
                print(f"ERROR: {e}")

        if parsed:
            validated = validate_evidence(parsed, all_criteria, note_text)
            per_note_results.append((filename, validated))
        else:
            per_note_results.append((filename, []))
            print(f"      Failed to parse JSON for note {note_idx + 1}")

    # Merge results across notes
    all_merged = merge_note_results(per_note_results, combined_text)

    # Add negated criteria as auto-met
    for nc in negated_criteria:
        if nc["id"] not in all_merged:
            all_merged[nc["id"]] = {"criterion_id": nc["id"], "met": True, "evidence": "No mention found"}

    met_ids = set(all_merged.keys())
    avg_tok_s = sum(total_tok_per_sec) / len(total_tok_per_sec) if total_tok_per_sec else 0

    return {
        "uuid": patient["uuid"],
        "name": patient["name"],
        "met_criteria_ids": sorted(met_ids),
        "inference_time_s": round(total_time, 1),
        "tokens_per_sec": round(avg_tok_s, 1),
    }


# ── Metrics computation ─────────────────────────────────────────────────────

def compute_metrics(model_results: list[dict], ground_truth: dict, criteria_ids: list[str]) -> dict:
    """Compute precision, recall, F1 against ground truth."""
    total_tp, total_fp, total_fn = 0, 0, 0
    per_criterion = {cid: {"tp": 0, "fp": 0, "fn": 0} for cid in criteria_ids}
    per_patient = []

    for result in model_results:
        uuid = result["uuid"]
        gt_patient = ground_truth["patients"].get(uuid)
        if not gt_patient:
            continue

        predicted_met = set(result["met_criteria_ids"])
        actual_met = {cid for cid, v in gt_patient["criteria"].items() if v["met"]}

        tp = predicted_met & actual_met
        fp = predicted_met - actual_met
        fn = actual_met - predicted_met

        total_tp += len(tp)
        total_fp += len(fp)
        total_fn += len(fn)

        for cid in criteria_ids:
            if cid in tp:
                per_criterion[cid]["tp"] += 1
            elif cid in fp:
                per_criterion[cid]["fp"] += 1
            elif cid in fn:
                per_criterion[cid]["fn"] += 1

        per_patient.append({
            "uuid": uuid,
            "name": result["name"],
            "tp": sorted(tp),
            "fp": sorted(fp),
            "fn": sorted(fn),
        })

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    per_criterion_recall = {}
    for cid, counts in per_criterion.items():
        denom = counts["tp"] + counts["fn"]
        per_criterion_recall[cid] = counts["tp"] / denom if denom > 0 else None

    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "tp": total_tp,
        "fp": total_fp,
        "fn": total_fn,
        "per_criterion_recall": per_criterion_recall,
        "per_patient": per_patient,
    }


# ── Output formatting ────────────────────────────────────────────────────────

def print_aggregate_table(all_metrics: dict[str, dict]):
    print(f"\n{'='*80}")
    print("AGGREGATE METRICS")
    print(f"{'='*80}")
    header = f"{'Model':<20} {'Precision':>10} {'Recall':>10} {'F1':>10} {'TP':>6} {'FP':>6} {'FN':>6}"
    print(header)
    print("-" * len(header))
    for model_name, metrics in all_metrics.items():
        print(f"{model_name:<20} {metrics['precision']:>10.3f} {metrics['recall']:>10.3f} "
              f"{metrics['f1']:>10.3f} {metrics['tp']:>6} {metrics['fp']:>6} {metrics['fn']:>6}")


def print_criterion_table(all_metrics: dict[str, dict], criteria_ids: list[str]):
    print(f"\n{'='*80}")
    print("PER-CRITERION RECALL")
    print(f"{'='*80}")

    model_names = list(all_metrics.keys())
    header = f"{'Criterion':<25}"
    for name in model_names:
        header += f" {name:>15}"
    print(header)
    print("-" * len(header))

    for cid in criteria_ids:
        row = f"{cid:<25}"
        for name in model_names:
            val = all_metrics[name]["per_criterion_recall"].get(cid)
            if val is None:
                row += f" {'N/A':>15}"
            else:
                row += f" {val:>15.2f}"
        print(row)


def print_performance_table(all_perf: dict[str, dict]):
    print(f"\n{'='*80}")
    print("PERFORMANCE")
    print(f"{'='*80}")
    header = (f"{'Model':<20} {'Load (s)':>10} {'Memory (MB)':>12} "
              f"{'Total (s)':>10} {'Avg/pat (s)':>12} {'Tok/s':>8}")
    print(header)
    print("-" * len(header))
    for model_name, perf in all_perf.items():
        print(f"{model_name:<20} {perf['load_time_s']:>10.1f} {perf['peak_memory_mb']:>12.0f} "
              f"{perf['total_inference_s']:>10.1f} {perf['avg_per_patient_s']:>12.1f} "
              f"{perf['avg_tok_per_sec']:>8.1f}")


def print_patient_breakdown(all_metrics: dict[str, dict]):
    for model_name, metrics in all_metrics.items():
        print(f"\n{'='*80}")
        print(f"PER-PATIENT BREAKDOWN: {model_name}")
        print(f"{'='*80}")
        for pat in metrics["per_patient"]:
            status = f"TP={len(pat['tp'])} FP={len(pat['fp'])} FN={len(pat['fn'])}"
            print(f"  {pat['name']:<35} {status}")
            if pat['fp']:
                print(f"    FP: {', '.join(pat['fp'])}")
            if pat['fn']:
                print(f"    FN: {', '.join(pat['fn'])}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Benchmark clinical extraction models")
    parser.add_argument("--models", nargs="*", default=None,
                        help=f"Models to benchmark (default: all). Choices: {', '.join(MODEL_CONFIGS.keys())}")
    parser.add_argument("--patients", nargs="*", default=None,
                        help="Patient names or UUIDs to test (default: all)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSON file (default: benchmark_results.json)")
    parser.add_argument("--ground-truth", type=str, default="ground_truth.json",
                        help="Ground truth JSON file (default: ground_truth.json)")
    parser.add_argument("--benchmark", action="store_true",
                        help="Shortcut: use benchmark_ground_truth.json and auto-filter to its patients")
    parser.add_argument("--run", type=int, choices=[1, 2, 3], default=None,
                        help="Run a pre-determined patient split (1: 16, 2: 16, 3: 15). Implies --benchmark")
    args = parser.parse_args()

    # --run implies --benchmark
    if args.run is not None:
        args.benchmark = True

    # --benchmark convenience flag
    if args.benchmark:
        if args.ground_truth == "ground_truth.json":  # not explicitly overridden
            args.ground_truth = "benchmark_ground_truth.json"
        if args.output is None:
            if args.run is not None:
                args.output = f"benchmark_run{args.run}_results.json"
            else:
                args.output = "benchmark_47_results.json"
    if args.output is None:
        args.output = "benchmark_results.json"

    # Augment name→UUID shortcuts from the selected ground truth
    _augment_uuids_from_ground_truth(args.ground_truth)

    # Determine models to run
    if args.models:
        model_names = []
        for m in args.models:
            matched = [k for k in MODEL_CONFIGS if k.lower() == m.lower()]
            if matched:
                model_names.append(matched[0])
            else:
                print(f"Unknown model: {m}. Available: {', '.join(MODEL_CONFIGS.keys())}")
                sys.exit(1)
    else:
        model_names = list(MODEL_CONFIGS.keys())

    # Load data
    print("Loading criteria...")
    tree, all_criteria, criteria, negated_criteria = load_criteria()
    criteria_ids = [c["id"] for c in all_criteria]
    print(f"  {len(all_criteria)} criteria ({len(criteria)} for model, {len(negated_criteria)} negated/auto-met)")

    print("Loading patients...")
    patients = load_patients()
    print(f"  {len(patients)} patients discovered")

    # Filter patients if specified
    if args.patients:
        test_uuids = []
        for name in args.patients:
            name_lower = name.lower()
            if name_lower in ALL_TEST_UUIDS:
                test_uuids.append(ALL_TEST_UUIDS[name_lower])
            elif name in patients:
                test_uuids.append(name)
            else:
                matched = [u for u, p in patients.items() if name_lower in p["name"].lower()]
                if matched:
                    test_uuids.extend(matched)
                else:
                    print(f"Unknown patient: {name}")
                    sys.exit(1)
    else:
        test_uuids = sorted(patients.keys())

    print(f"  Testing {len(test_uuids)} patient(s)")

    # Load ground truth
    print(f"Loading ground truth from {args.ground_truth}...")
    with open(args.ground_truth) as f:
        ground_truth = json.load(f)
    print(f"  {len(ground_truth['patients'])} patients in ground truth")

    # --benchmark: auto-filter to only patients present in ground truth
    if args.benchmark and not args.patients:
        gt_uuids = set(ground_truth["patients"].keys())
        test_uuids = [u for u in test_uuids if u in gt_uuids]
        print(f"  Filtered to {len(test_uuids)} patient(s) with ground truth")

    # --run: use pre-determined patient split
    if args.run is not None:
        test_uuids = BENCHMARK_RUNS[args.run]
        print(f"  Run {args.run}: {len(test_uuids)} patient(s)")

    # Run each model
    all_model_results = {}
    all_model_metrics = {}
    all_model_perf = {}

    for model_name in model_names:
        config = MODEL_CONFIGS[model_name]
        print(f"\n{'#'*80}")
        print(f"# MODEL: {model_name}")
        print(f"# ID: {config['model_id']}")
        print(f"{'#'*80}")

        backend = config["backend_cls"](config["model_id"])
        formatter = config["formatter_cls"]()

        # Load model
        print(f"\nLoading {model_name}...")
        try:
            load_info = backend.load()
        except Exception as e:
            print(f"FAILED to load {model_name}: {e}")
            traceback.print_exc()
            continue
        print(f"  Loaded in {load_info['load_time_s']}s, peak memory: {load_info['peak_memory_mb']:.0f} MB")

        # Run extraction for each patient
        patient_results = []
        for uuid in test_uuids:
            pat = patients.get(uuid)
            if not pat:
                print(f"  Skipping unknown UUID: {uuid}")
                continue

            print(f"\n  Patient: {pat['name']} ({uuid[:8]})")
            result = extract_patient(
                backend, formatter, criteria, all_criteria, negated_criteria, pat,
            )
            patient_results.append(result)
            print(f"    Met: {len(result['met_criteria_ids'])}/{len(criteria_ids)} — "
                  f"{', '.join(c.split('.')[-1] for c in result['met_criteria_ids'])}")

        # Compute metrics
        metrics = compute_metrics(patient_results, ground_truth, criteria_ids)

        # Compute performance aggregates
        total_inference = sum(r["inference_time_s"] for r in patient_results)
        avg_per_patient = total_inference / len(patient_results) if patient_results else 0
        avg_tok_s = (sum(r["tokens_per_sec"] for r in patient_results) /
                     len(patient_results) if patient_results else 0)

        perf = {
            "load_time_s": load_info["load_time_s"],
            "peak_memory_mb": load_info["peak_memory_mb"],
            "total_inference_s": round(total_inference, 1),
            "avg_per_patient_s": round(avg_per_patient, 1),
            "avg_tok_per_sec": round(avg_tok_s, 1),
        }

        all_model_results[model_name] = patient_results
        all_model_metrics[model_name] = metrics
        all_model_perf[model_name] = perf

        # Unload model before loading next
        print(f"\nUnloading {model_name}...")
        backend.unload()
        time.sleep(2)

    # Print comparison tables
    if all_model_metrics:
        print_aggregate_table(all_model_metrics)
        print_criterion_table(all_model_metrics, criteria_ids)
        print_performance_table(all_model_perf)
        print_patient_breakdown(all_model_metrics)

    # Save full results
    output = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "num_patients": len(test_uuids),
            "criteria_ids": criteria_ids,
        },
        "models": {},
    }
    for model_name in model_names:
        if model_name in all_model_results:
            output["models"][model_name] = {
                "model_id": MODEL_CONFIGS[model_name]["model_id"],
                "metrics": all_model_metrics[model_name],
                "performance": all_model_perf[model_name],
                "patient_results": all_model_results[model_name],
            }

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
