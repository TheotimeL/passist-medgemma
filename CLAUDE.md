# MedGemma PA Form Auto-Fill Pipeline

## TOP PRIORITY RULE

**NEVER HARDCODE ANYTHING. NEVER MANUALLY MAP SPECIFIC STRINGS OR KEYWORDS BASED ON CONTEXT.** All mappings, labels, categories, and logic must come from the data itself (policy tree, FHIR bundles, LLM output) or be derived programmatically. If a value needs to be determined, extract it from the source data or use the AI — do NOT invent manual mappings based on what "seems right." This applies to field names, criterion IDs, clinical terms, drug names, section labels, and any other domain-specific strings. Code translation tables (SNOMED→ICD-10, Drug→HCPCS) that come from official standards are the only acceptable static mappings.

## Project Overview

End-to-end pipeline that extracts clinical evidence from patient notes using a local MedGemma 27B model, evaluates it against an insurance policy decision tree, and auto-fills Prior Authorization (PA) PDF forms. Includes a full web UI for doctor review.

**Constraints:**
- All processing is LOCAL — no external API calls (no NPI registry, UMLS, etc.)
- NO disease-specific hardcoding — all clinical logic comes from the policy tree or LLM output
- Code translation tables (SNOMED→ICD-10, Drug→HCPCS) are acceptable as reference data
- "AI drafts, human verifies" — ~75% of fields auto-filled, ~15 left for doctor

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          OFFLINE PIPELINE (one-time)                            │
│                                                                                 │
│  UHC Policy PDF ──→ scripts/extract_policy.py                                  │
│                      │                                                          │
│                      ├─ Step 1: PyMuPDF ──→ raw text (~38K chars)              │
│                      ├─ Step 2: Gemini 2.0-Flash ──→ cleaned RA section        │
│                      ├─ Step 3: LangExtract + Gemini 2.5-Flash ──→ .jsonl      │
│                      ├─ Step 4: Tree reconstruction ──→ nested AND/OR tree     │
│                      └─ Step 5: Gemini 2.5-Flash ──→ enriched tree             │
│                                                      (keywords/anti_keywords)   │
│                                                           │                     │
│                         ┌─────────────────────────────────┤                     │
│                         ▼                                 ▼                     │
│            decision_tree.json              decision_tree_enriched.json          │
│            (short descriptions,            (keywords, anti_keywords,            │
│             used in MedGemma prompt)        used in validation + UI)            │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                              DATA SOURCES (all local)                           │
│                                                                                 │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────────┐  │
│  │ FHIR Bundles     │  │ SOAP Notes       │  │ PA Form Templates            │  │
│  │ (Synthea)        │  │                  │  │                              │  │
│  │ generations/     │  │ notes/{uuid}/    │  │ BCBS TX form (downloaded)    │  │
│  │   output_*/fhir/ │  │   metadata.json  │  │ pa_form_*.pdf (local)       │  │
│  │ fhir/            │  │   *.txt          │  │                              │  │
│  │   (deploy copy)  │  │ soap_notes/      │  │                              │  │
│  │                  │  │   (legacy)       │  │                              │  │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────────┬───────────────┘  │
│           │                     │                            │                  │
└───────────┼─────────────────────┼────────────────────────────┼──────────────────┘
            │                     │                            │
            ▼                     ▼                            ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        FASTAPI BACKEND (server.py :8000)                        │
│                                                                                 │
│  ┌─── Lifespan Startup ─────────────────────────────────────────────────────┐   │
│  │ load_dotenv() → check SKIP_MODEL / EXTRACTION_BACKEND                   │   │
│  │ → ExtractionService.get_instance().initialize()                          │   │
│  │   → load MedGemma 27B (mlx-lm) or Gemini backend                       │   │
│  │   → cache KV prefix (instructions + criteria + few-shot)                │   │
│  │   → discover patients from notes/ + soap_notes/                          │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─── API Routers (/api) ───────────────────────────────────────────────────┐   │
│  │                                                                          │   │
│  │  api/patients.py                api/extraction.py                        │   │
│  │  ├ GET /patients                ├ GET /patients/{uuid}/extract  (SSE)    │   │
│  │  ├ GET /patients/{uuid}/fhir    └ GET /patients/{uuid}/extraction        │   │
│  │  └ GET /patients/{uuid}/notes                                            │   │
│  │                                                                          │   │
│  │  api/form.py                                                             │   │
│  │  ├ GET  /form/field-mapping                                              │   │
│  │  ├ GET  /policy/tree                                                     │   │
│  │  ├ GET  /policy/criteria                                                 │   │
│  │  ├ POST /patients/{uuid}/justification                                   │   │
│  │  └ POST /form/generate-pdf                                               │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─── Core Services ────────────────────────────────────────────────────────┐   │
│  │                                                                          │   │
│  │  extraction_service.py (Singleton)                                       │   │
│  │  ├ _build_prompt_prefix() → static instruction + criteria + few-shot     │   │
│  │  ├ _build_prompt_suffix() → patient note text                            │   │
│  │  ├ _run_inference()       → deep-copy KV cache → mlx_lm.generate()      │   │
│  │  ├ _parse_json_response() → chain-of-thought → JSON extraction          │   │
│  │  ├ validate_evidence()    → spam/ngram/keyword/anti-keyword filters      │   │
│  │  └ extract(uuid)          → generator yielding SSE events                │   │
│  │         │                                                                │   │
│  │         │ GPU Lock (asyncio.Lock) — serializes Metal GPU access          │   │
│  │         │                                                                │   │
│  │  policy_tree.py                                                          │   │
│  │  ├ PolicyNode / PolicyStatus / CriterionResult (dataclasses)             │   │
│  │  ├ load_tree() → parse JSON → recursive PolicyNode tree                  │   │
│  │  ├ get_all_criteria() → flat dict of LEAF nodes                          │   │
│  │  ├ evaluate() → recursive AND/OR logic (negated inverts)                 │   │
│  │  └ get_status() → overall eligibility + per-criterion status             │   │
│  │                                                                          │   │
│  │  patient_data.py                                                         │   │
│  │  ├ load_patient_from_fhir() → parse FHIR bundle → patient dict          │   │
│  │  ├ format_section_vi()      → clinical justification letter              │   │
│  │  ├ fill_pa_form()           → orchestrate full PDF filling               │   │
│  │  └ Static tables: SNOMED_TO_ICD10, DRUG_TO_HCPCS                        │   │
│  │                                                                          │   │
│  │  pdf_form.py (PDFFormManager)                                            │   │
│  │  ├ Load template (URL or local path)                                     │   │
│  │  ├ set_field() / set_checkbox()                                          │   │
│  │  └ generate_pdf() → AcroForm fill with appearance streams                │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─── MedGemma Inference Flow ──────────────────────────────────────────────┐   │
│  │                                                                          │   │
│  │   Cached KV Prefix ──┐                                                   │   │
│  │   (instructions +    ├──→ Deep Copy ──→ mlx_lm.generate() ──→ CoT + JSON│   │
│  │    criteria +        │    per request    temp=0.1, top_p=0.9             │   │
│  │    few-shot example) │    ↑              max_tokens=8000                 │   │
│  │                      │    │                     │                        │   │
│  │   Patient Note ──────┘    │                     ▼                        │   │
│  │   (suffix only)      GPU Lock           parse + validate                │   │
│  │                                                 │                        │   │
│  │                                    ┌────────────┼──────────────┐         │   │
│  │                                    ▼            ▼              ▼         │   │
│  │                              Spam filter   N-gram 50%    Keyword/        │   │
│  │                              (≥3 reuse)    grounding     anti-keyword    │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
            │
            │  SSE Stream + REST JSON + PDF FileResponse
            │  (Vite dev proxy: /api → :8000)
            ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    VUE 3 FRONTEND (Vite :5173)                                  │
│                                                                                 │
│  ┌─── Pinia Stores ─────────────────────────────────────────────────────────┐   │
│  │                                                                          │   │
│  │  patient.ts                extraction.ts              form.ts            │   │
│  │  ├ patients[]              ├ results[]                ├ fields{}         │   │
│  │  ├ fhirData                ├ policyStatus             ├ justification    │   │
│  │  ├ notes[]                 ├ overrides{}              ├ buildFromFhir()  │   │
│  │  ├ fetchPatients()         │  (doctor overrides)      ├ addExtraction()  │   │
│  │  ├ fetchFhir()             ├ criterionReviews{}       ├ accept/reject/   │   │
│  │  └ fetchNotes()            ├ treeEligibility            edit/undo()     │   │
│  │                            ├ fetchExtraction()        └ fetchJustify()   │   │
│  │                            │  (SSE or pre-computed)                      │   │
│  │                            ├ setOverride()                               │   │
│  │                            ├ reviewAccept/Reject()                       │   │
│  │                            └ OR-aware review counting                    │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─── Pages ────────────────────────────────────────────────────────────────┐   │
│  │                                                                          │   │
│  │  index.vue (Landing)              workspace/[uuid].vue (Main)            │   │
│  │  ├ Patient dropdown               ├ Split panel layout (38% / 62%)       │   │
│  │  ├ Insurer (BCBS/UHC)             ├ Left: ReviewQueue | Clinical Notes  │   │
│  │  ├ Drug (Adalimumab)              ├ Right: Notes | PA Form | Policy |   │   │
│  │  └ → Navigate to workspace        │         Justification               │   │
│  │                                    ├ onMounted: FHIR → fields → SSE     │   │
│  │                                    └ Watchers: results → fields → PDF    │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─── Components ───────────────────────────────────────────────────────────┐   │
│  │                                                                          │   │
│  │  ReviewQueue.vue           PolicyTree.vue          NoteViewer.vue        │   │
│  │  ├ Grouped form fields     ├ AND/OR gate eval      ├ Multi-note tabs    │   │
│  │  ├ Source badges           ├ Eligibility banner     └ Evidence highlight │   │
│  │  │ (FHIR/AI/Manual)       ├ Blocker list                                │   │
│  │  ├ Accept/Reject/Edit      ├ Review progress bar   PdfViewer.vue        │   │
│  │  └ "View in Notes" link    └ Emit viewSource       ├ PDF from blob      │   │
│  │                                    │                └ Clickable fields   │   │
│  │  PolicyTreeNode.vue ◄──────────────┘ (recursive)                        │   │
│  │  ├ Gate: AND/OR badge + met counter                                      │   │
│  │  ├ Leaf: evidence + status (green/red/auto)                             │   │
│  │  ├ Resolve (mark NOT_MET → MET) / Override (reject AUTO_MET)            │   │
│  │  └ Override → injects evidence into justification letter                 │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                           UX FLOW (end-to-end)                                  │
│                                                                                 │
│  1. Landing: Select patient + insurer + drug                                    │
│  2. Workspace loads → FHIR fields populate instantly (source: FHIR)             │
│  3. SSE extraction starts → progressive AI results fill step therapy fields     │
│  4. Policy tree evaluates AND/OR eligibility → banner shows status              │
│  5. Doctor reviews each field (accept/reject/edit) and each criterion           │
│  6. Doctor can override policy criteria (resolve blockers / reject auto-met)    │
│  7. Justification letter auto-generated, editable by doctor                     │
│  8. PDF preview auto-regenerates on field changes                               │
│  9. Download final PA form PDF                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                     KEY ARCHITECTURAL PATTERNS                                  │
│                                                                                 │
│  • KV Cache Reuse: Static prompt prefix cached in GPU memory; only patient      │
│    note changes per inference (~50% latency reduction)                           │
│  • GPU Lock: asyncio.Lock serializes Metal GPU access for concurrent requests   │
│  • Override Priority: Doctor overrides > Negated (auto-met) > AI results        │
│  • OR-Aware Review: Only best OR branch counted for review progress             │
│  • Dual Trees: Original (short, for prompt) + Enriched (keywords, for UI)       │
│  • No Disease Hardcoding: All clinical logic from policy tree or LLM output     │
│  • Evidence Validation: 5-stage pipeline (spam, n-gram, keyword, anti-kw, etc.) │
│  • SSE Bridge: sync generator → asyncio.Queue → async SSE stream               │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### SSE Event Flow Detail

```
Browser EventSource                    FastAPI                     ExtractionService
      │                                   │                              │
      │── GET /api/patients/{uuid}/extract ──→                           │
      │                                   │── acquire GPU lock ──→       │
      │                                   │── run_in_executor() ────→    │
      │                                   │                     extract(uuid)
      │                                   │                        │
      │   ◄── event: status ──────────────│◄── queue.put("status") │
      │   {"message": "Note 1/2..."}      │                        │
      │                                   │            _run_inference()
      │                                   │            _parse_json_response()
      │                                   │            validate_evidence()
      │   ◄── event: run ────────────────│◄── queue.put("run") ───│
      │   {"run":1, "criteria":[...]}     │                        │
      │                                   │                        │
      │   ◄── event: status ──────────────│◄── queue.put("status") │
      │   {"message": "Note 2/2..."}      │                        │
      │                                   │            _run_inference()
      │   ◄── event: run ────────────────│◄── queue.put("run") ───│
      │   {"run":2, "criteria":[...]}     │                        │
      │                                   │            merge + get_status()
      │   ◄── event: policy ─────────────│◄── queue.put("policy") │
      │   {"overall":null, "met_count":5} │                        │
      │                                   │                        │
      │   ◄── event: complete ───────────│◄── queue.put("complete")│
      │   {"eligible":false, ...}         │── release GPU lock     │
      │                                   │                        │
      ▼                                   ▼                        ▼
  extraction.ts                     StreamingResponse         gc + mx.clear
  updates results/status
  → triggers form field population
  → triggers policy tree re-evaluation
  → triggers justification generation
```

## Policy Extraction Pipeline

The offline pipeline that converts a raw insurance policy PDF into decision tree JSONs is in `scripts/extract_policy.py`. Uses Google Gemini (not MedGemma) for extraction since this is a one-time step. See that file for details on the 5-stage pipeline (PyMuPDF → Gemini text cleaning → LangExtract → tree reconstruction → clinical enrichment).

## Key Files

### Backend
| File | Purpose |
|---|---|
| `server.py` | FastAPI entry point, model loading at startup (lifespan) |
| `services/extraction_service.py` | Singleton wrapping MedGemma extraction for web use (SSE streaming) |
| `services/patient_data.py` | FHIR parser, Section VI/IX formatter |
| `services/policy_tree.py` | PolicyNode/PolicyStatus dataclasses, tree loading, evaluation, ID assignment + enrichment |
| `services/pdf_form.py` | PDFFormManager class (pypdf AcroForm filling) |
| `services/drug_field_parser.py` | Drug field parsing with MedGemma 4B |
| `api/patients.py` | Patient listing, FHIR data, SOAP notes, justification endpoints |
| `api/extraction.py` | SSE live extraction + pre-computed results fallback |
| `api/form.py` | PDF generation endpoint |
| `scripts/extract_policy.py` | Policy PDF → decision tree pipeline (offline, PyMuPDF + LangExtract + Gemini) |
| `scripts/benchmark_models.py` | Model benchmarking (MedGemma vs Meditron vs LLaMA) |

### Frontend (`frontend/src/`)
| File | Purpose |
|---|---|
| `pages/index.vue` | Landing page — patient/insurer/drug selection |
| `pages/workspace/[uuid].vue` | Main workspace — split panel layout, tab management |
| `components/ReviewQueue.vue` | Left panel — grouped form fields with accept/reject/edit |
| `components/PolicyTree.vue` | Policy tree with AND/OR evaluation, eligibility banner |
| `components/PolicyTreeNode.vue` | Recursive tree node with override (resolve/reject) UI |
| `components/NoteViewer.vue` | SOAP note display with evidence highlighting |
| `stores/patient.ts` | Pinia store — FHIR data, notes, patient list |
| `stores/extraction.ts` | Pinia store — SSE state, results, overrides, tree eligibility |
| `stores/form.ts` | Pinia store — form fields, accept/reject/edit state |

### Data & Config
| File | Purpose |
|---|---|
| `rheumatoid_arthritis_initial_auth_decision_tree_enriched.json` | Enriched policy tree with keywords/anti_keywords |
| `rheumatoid_arthritis_initial_auth_decision_tree.json` | Original tree (shorter descriptions, used in prompt) |
| `rheumatoid_arthritis_initial_auth_clean.txt` | Cleaned policy text (RA section only) |
| `rheumatoid_arthritis_initial_auth_visualization.html` | LangExtract HTML visualization of extractions |

## Data Sources (all local)

| Source | Location |
|---|---|
| Policy PDF (input) | `UHC_Commercial_Medical_Policy_Adalimumab.pdf` |
| FHIR bundles (Synthea) | `generations/output_{region}/fhir/*.json` |
| SOAP notes | `soap_notes/*.txt` |
| Clinical notes | `clinical_notes/*.txt` |
| Extraction outputs | `extraction_vanesa.json`, `extraction_analisa.json`, etc. |
| PA PDF templates | `pa_form_*.pdf` |

## Web UI

### Stack
- **Frontend**: Vue 3 (Composition API) + TypeScript + Vuetify 3 (Material Design) + Pinia + Vue Router
- **Backend**: FastAPI + Uvicorn
- **Design**: Google Material Design — clean, Google color palette, rounded cards

### Running the Web UI

```bash
# Backend WITH live model (full demo):
uv run uvicorn server:app --port 8000

# Backend WITHOUT model (pre-computed results only):
SKIP_MODEL=1 uv run uvicorn server:app --port 8000

# Frontend (Vite dev server, proxies /api → localhost:8000):
cd frontend && npm run dev
# Open http://localhost:5173
```

### UX Flow

1. **Landing Page**: Select patient, insurer (UHC/BCBS), drug → "Start Authorization Review"
2. **Workspace** (split panels):
   - **Left panel**: Review Queue with grouped form fields by section
     - Patient Info, Provider, Diagnosis (FHIR, instant)
     - Step Therapy / Drug History (AI-extracted, ~22s)
     - Drug Request (AI-extracted)
     - Each field: value + source badge (FHIR/AI/Manual) + Accept/Reject/Edit buttons
     - Empty manual fields clickable to enter values
   - **Right panel** (tabs):
     - **PA Form**: Live PDF preview with auto-regeneration on field changes
     - **Clinical Notes**: SOAP note with evidence highlighting ("View in Notes")
     - **Policy**: Interactive decision tree with AND/OR evaluation
     - **Justification**: Criteria sidebar + editable clinical justification letter

### Policy Tree Features
- AND/OR gate evaluation with recursive tree logic
- Doctor overrides: "Resolve" (mark NOT MET → MET) or "Override" (reject AUTO-MET)
- Undo overrides to restore AI/auto-met results
- Eligibility banner updates in real-time with override count
- Blocker identification shows which criteria are preventing eligibility

### Form Field States
- `suggested` → initial AI/FHIR value, pending review
- `accepted` → doctor approved
- `rejected` → doctor rejected, value cleared
- `edited` → doctor modified the value

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Health check + model status |
| `GET` | `/api/patients` | List patients from soap_notes/ |
| `GET` | `/api/patients/{uuid}/fhir` | FHIR demographics |
| `GET` | `/api/patients/{uuid}/notes` | SOAP note text |
| `GET` | `/api/patients/{uuid}/extract` | **SSE stream** — live MedGemma extraction |
| `GET` | `/api/patients/{uuid}/justification` | Generated clinical justification letter |
| `GET` | `/api/policy/tree` | Policy decision tree JSON |
| `POST` | `/api/form/generate-pdf` | Generate filled PDF from field values |

### SSE Event Stream (`/api/patients/{uuid}/extract`)
Uses asyncio.Queue to bridge sync generator → async SSE for progressive updates:
```
event: status    → {"message": "Extraction run 1/2..."}
event: run       → {"run": 1, "criteria": [...partial results...], "time_s": 11.2}
event: status    → {"message": "Extraction run 2/2..."}
event: run       → {"run": 2, "criteria": [...merged results...], "time_s": 10.8}
event: policy    → {"overall": null, "met_count": 5, "total_count": 7, ...}
event: complete  → {"met_criteria": [...], "eligible": false, "inference_time_s": 22.0}
```

## MedGemma Extraction Pipeline

- **Model**: `mlx-community/medgemma-27b-text-it-bf16` (local MLX)
- **Prompt caching**: Static prefix (instructions + criteria + few-shot example) cached as KV, only patient note changes per inference
- **Two-run merge**: Each patient gets 2 inference runs, results merged (keeps longer evidence on collision)
- **Chain-of-thought**: Model reasons through each criterion before outputting JSON
- **Enriched JSON fields** per criterion:
  - `criterion_id`, `met`, `evidence` (required)
  - `drug_name`, `drug_dose`, `drug_dates`, `is_prior_therapy`, `failure_reason` (drug criteria)
  - `prescriber_name`, `prescriber_specialty` (prescriber criteria)

## Evidence Validation Pipeline

1. `met=true` filtering (only keep met criteria)
2. Empty/NOTHING rejection
3. Spam detection (same evidence reused for 3+ criteria)
4. N-gram grounding (50% of evidence 4-grams must appear in note)
5. Anti-keyword check (anti_keywords >= positive keywords → reject)
6. Note-grounding (at least one positive keyword must exist in the note)
7. Source-text entity grounding (evidence must mention specific entities from criterion)

## Form Filling (`patient_data.py`)

### Design Principles
- Drug history table uses ONLY LLM-provided `drug_name` — no keyword heuristic fallback
- `is_prior_therapy` from LLM decides what goes in drug history (not criterion ID patterns)
- Prescriber specialty: prefers LLM-extracted, falls back to tree keyword inference
- Requested drug/HCPCS: inferred dynamically from policy tree (`_infer_requested_drug`)
- Supports both UHC and BCBS TX form layouts (dual field name conventions)

### Key Functions
- `load_patient_from_fhir(bundle_path)` → structured patient dict
- `format_section_vi(patient_data, policy_status, extraction_results, tree)` → clinical letter
- `fill_pa_form(pdf, patient_data, policy_status, extraction_results, tree, output_path)` → PDF path

## Supported Forms

| Form | Fields | Clinical Justification Field |
|---|---|---|
| UHC TX | `Patient Name`, `Paitent Gender - Male` (sic), etc. | `SECTION VI  CLINICAL DOCUMENTATION...` |
| BCBS TX | `Patient's Name`, `Patient's Gender - Male`, etc. | `Section IX ― Justification...` (U+2015) |

## Known Issues & Decisions

- MedGemma sometimes sets `is_prior_therapy: true` on diagnosis criteria (not a drug) — handled by requiring LLM-provided `drug_name` for drug history entries
- Prescriber criterion (`prescrib.4`) sometimes filtered out by validation when evidence is just a name without department — model needs to extract full signature block
- `eligible=None` (PENDING) when not all criteria met — tree uses OR branches for step therapy, so 5/7 may still be eligible depending on which branches
- Policy tree `negated` criteria are auto-met (absence of evidence = criterion satisfied)
- PDF multiline fields use `\r\n` line endings for proper AcroForm rendering
- Justification letter is generated once after extraction and doesn't auto-update with doctor overrides (doctor can manually edit)
- Override priority order: doctor overrides → negated (auto-met) → AI results (checked in this order everywhere)

## Test Patients

| Name | UUID (short) | Status |
|---|---|---|
| Vanesa Sindy Thiel | `520e6e72` | 5/7 met, ELIGIBLE candidate |
| Analisa Auer | `affb0758` | 3/7 met, NOT ELIGIBLE |
| Broderick Hackett | `2d701350` | Available |
| Aide Reichel | `5c9df1d3` | Available |
| Caridad Zaragoza | `92181936` | Available |
| Alycia Hodkiewicz | `aa20b461` | Available |
| Antonio Tello | `1a691f1f` | Available |
| Abram Gerlach | `8b8f1e13` | Available |
