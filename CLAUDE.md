# MedGemma PA Form Auto-Fill Pipeline

## Project Overview

End-to-end pipeline that extracts clinical evidence from patient notes using a local MedGemma 27B model, evaluates it against an insurance policy decision tree, and auto-fills Prior Authorization (PA) PDF forms. Includes a full web UI for doctor review.

**Constraints:**
- All processing is LOCAL — no external API calls (no NPI registry, UMLS, etc.)
- NO disease-specific hardcoding — all clinical logic comes from the policy tree or LLM output
- Code translation tables (SNOMED→ICD-10, Drug→HCPCS) are acceptable as reference data
- "AI drafts, human verifies" — ~75% of fields auto-filled, ~15 left for doctor

## Architecture

```
  Policy PDF ──→ extract_policy.py ──→ Policy Tree JSONs
  (Gemini)        (PyMuPDF + LangExtract)   │
                                             │
                          ┌─────────────────────────────────────────┐
                          │           Web UI (Vue 3 + Vuetify)      │
                          │  Landing Page → Workspace (split panel) │
                          └──────────────┬──────────────────────────┘
                                         │ /api/*
                          ┌──────────────▼──────────────────────────┐
                          │        FastAPI Backend (server.py)       │
                          │   api/patients.py  api/extraction.py     │
                          │   api/form.py      extraction_service.py │
                          └──────────────┬──────────────────────────┘
                                         │
          ┌──────────────┬───────────────┼───────────────┐
          ▼              ▼               ▼               ▼
   FHIR Bundles     SOAP Notes     Policy Tree     MedGemma 27B
  patient_data.py   soap_notes/    policy_tree.py  (MLX local)
                                                        │
                                                        ▼
                                                   PDF Form Fill
                                                   pdf_form.py
```

## Policy Extraction Pipeline (`extract_policy.py`)

Converts a raw insurance policy PDF into the structured decision tree JSONs used by the rest of the system. Uses Google Gemini (not MedGemma) for extraction since this is a one-time offline step.

```
UHC_Commercial_Medical_Policy_Adalimumab.pdf
        │
        ▼  Step 1: PyMuPDF (fitz)
  Raw text (~38K chars)
        │
        ▼  Step 2: Gemini 2.0-Flash — text cleaning
  RA-specific section (~2.8K chars)
  (headers/footers removed, numbering preserved)
        │
        ▼  Step 3: LangExtract + Gemini 2.5-Flash — structured extraction
  .jsonl with 31 items:
  LogicGates (AND/OR), Criteria (leaf nodes), EvidenceRequirements
  Each with logic_path encoding tree position
        │
        ▼  Step 4: Tree reconstruction
  Nested AND/OR tree with auto-generated IDs
  → rheumatoid_arthritis_initial_auth_decision_tree.json
        │
        ▼  Step 5: Clinical enrichment — Gemini 2.5-Flash
  keywords, anti_keywords, search_descriptions per leaf
  → rheumatoid_arthritis_initial_auth_decision_tree_enriched.json
```

### Running
```bash
# Full pipeline: PDF → clean text → extract → tree → enrich
python extract_policy.py

# Enrich an existing tree only
python extract_policy.py --enrich-only <tree.json> --enrich-output <enriched.json>
```

### Key Design Decisions
- **LangExtract** ensures consistent tree shape without brittle regex parsing
- **Few-shot examples** use generic policy text to bias structure without leaking specifics
- **No hardcoded disease logic** — works for any disease/drug/payer combination
- **Two tree outputs**: original (shorter, used in MedGemma prompt) and enriched (keywords, used in validation pipeline)

## Key Files

### Backend
| File | Purpose |
|---|---|
| `server.py` | FastAPI entry point, model loading at startup (lifespan) |
| `extraction_service.py` | Singleton wrapping MedGemma extraction for web use (SSE streaming) |
| `api/patients.py` | Patient listing, FHIR data, SOAP notes, justification endpoints |
| `api/extraction.py` | SSE live extraction + pre-computed results fallback |
| `api/form.py` | PDF generation endpoint |
| `patient_data.py` | FHIR parser, Section VI/IX formatter, form fill orchestrator |
| `policy_tree.py` | PolicyNode/PolicyStatus dataclasses, tree loading, evaluation, ID assignment + enrichment |
| `pdf_form.py` | PDFFormManager class (pypdf AcroForm filling) |
| `extract_policy.py` | Policy PDF → decision tree pipeline (PyMuPDF + LangExtract + Gemini) |

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
| `rheumatoid_arthritis_initial_auth_extractions.jsonl` | Flat LangExtract output (31 items with logic_path) |
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
| `GET` | `/api/patients/{uuid}/extraction` | Pre-computed extraction results |
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

## Running (CLI)

```bash
# Extract evidence for a specific patient
python test_extraction_soap.py vanesa --output extraction_vanesa.json

# Extract all patients
python test_extraction_soap.py --output all_results.json
```

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
