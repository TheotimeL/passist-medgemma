# MedGemma PA Form Auto-Fill

AI-powered Prior Authorization form filling using local clinical evidence extraction.

## What It Does

This pipeline extracts clinical evidence from patient notes using a local MedGemma 27B model, evaluates it against an insurance policy decision tree (AND/OR logic), and auto-fills Prior Authorization PDF forms. A web UI lets physicians review AI-extracted fields, override policy criteria, and download the completed PA form — all running locally with no external API calls.

**Key idea:** AI drafts ~75% of form fields from FHIR records and clinical notes. The doctor reviews, accepts/rejects/edits each field, then downloads the filled PDF.

## Quick Start

### Prerequisites

- Python 3.11+ with [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- Apple Silicon Mac (for local MedGemma via MLX) — or use Gemini backend

### Setup

```bash
# Clone and install Python dependencies
git clone <repo-url> && cd medgemma-impact-tibotimz
cp .env.example .env   # Fill in your HF_TOKEN

# Install frontend dependencies
cd frontend && npm install && cd ..
```

### Run (Demo Mode — no GPU required)

```bash
# Backend with pre-computed extraction results
SKIP_MODEL=1 uv run uvicorn server:app --port 8000

# Frontend (in another terminal)
cd frontend && npm run dev
```

Open http://localhost:5173, select a patient, and explore the full workflow.

### Run (Full Mode — Apple Silicon)

```bash
# Backend with live MedGemma 27B inference
uv run uvicorn server:app --port 8000

# Frontend
cd frontend && npm run dev
```

## Architecture

```
FHIR Bundles (Synthea)  ─┐
                          ├──→ FastAPI Backend (:8000) ──→ Vue 3 Frontend (:5173)
Clinical Notes (SOAP)   ─┘         │
                                   ├─ MedGemma 27B (MLX, local)
Policy Decision Tree ──────────────┤   └─ KV cache reuse for fast inference
                                   ├─ Evidence validation (5-stage pipeline)
PA Form Template (PDF) ────────────┤   └─ Spam, n-gram grounding, keyword checks
                                   └─ PDF generation (pypdf AcroForm)
```

### Data Flow

1. **FHIR fields** populate instantly (patient demographics, provider info, diagnosis)
2. **MedGemma extraction** runs per-note via SSE, progressively filling step therapy fields
3. **Policy tree** evaluates AND/OR eligibility with each extraction update
4. **Doctor reviews** each field (accept/reject/edit) and each policy criterion
5. **Justification letter** auto-generated from met criteria evidence
6. **PDF** auto-regenerates on field changes, downloadable after attestation

## Project Structure

```
├── server.py                  # FastAPI entry point, model loading
├── extraction_service.py      # MedGemma extraction singleton (SSE streaming)
├── patient_data.py            # FHIR parser, clinical justification formatter
├── policy_tree.py             # Policy decision tree (AND/OR evaluation)
├── pdf_form.py                # PDF AcroForm filling (pypdf)
├── config.py                  # Paths, constants
├── api/
│   ├── patients.py            # Patient listing, FHIR data, notes
│   ├── extraction.py          # SSE extraction + pre-computed fallback
│   └── form.py                # PDF generation, field mapping, policy endpoints
├── frontend/src/
│   ├── pages/
│   │   ├── index.vue          # Landing — patient/insurer/drug selection
│   │   └── workspace/[uuid].vue  # Main workspace — split panel layout
│   ├── components/
│   │   ├── ReviewQueue.vue    # Form field review (accept/reject/edit)
│   │   ├── PolicyTree.vue     # AND/OR policy evaluation with overrides
│   │   ├── NoteViewer.vue     # Clinical notes with evidence highlighting
│   │   └── PdfViewer.vue      # Live PDF preview with clickable fields
│   └── stores/
│       ├── patient.ts         # FHIR data, notes
│       ├── extraction.ts      # SSE state, results, overrides
│       └── form.ts            # Form fields, justification
├── scripts/
│   └── extract_policy.py      # Policy PDF → decision tree pipeline (offline)
├── notes/                     # Patient clinical notes (per-UUID directories)
├── fhir/                      # FHIR bundles (Synthea-generated)
└── *.json                     # Decision trees, extraction results
```

## Key Design Decisions

- **No external API calls** — all processing runs locally (MedGemma on Metal GPU)
- **No disease-specific hardcoding** — all clinical logic from policy tree or LLM
- **KV cache reuse** — static prompt prefix cached in GPU memory; only patient note changes per inference (~50% latency reduction)
- **Two-run merge** — each patient gets 2 inference runs, results merged for better recall
- **OR-aware evaluation** — policy tree supports AND/OR gates; only best OR branch counts
- **Override priority** — doctor overrides > negated (auto-met) > AI results
- **5-stage evidence validation** — spam detection, n-gram grounding, keyword/anti-keyword filtering

## Tech Stack

| Layer | Technology |
|---|---|
| LLM | MedGemma 27B (mlx-lm on Apple Silicon) or Gemini 2.5 Flash |
| Backend | FastAPI + Uvicorn |
| Frontend | Vue 3 + TypeScript + Vuetify 3 + Pinia |
| PDF | pypdf (AcroForm filling) |
| Data | Synthea FHIR bundles, SOAP notes |

## Environment Variables

See `.env.example` for all options:

| Variable | Required | Description |
|---|---|---|
| `HF_TOKEN` | For MLX | Hugging Face token for model download |
| `LANGEXTRACT_API_KEY` | For Gemini | Google AI API key |
| `EXTRACTION_BACKEND` | No | `mlx` (default) or `gemini` |
| `SKIP_MODEL` | No | Set to `1` for demo mode (pre-computed results) |

## Detailed Architecture

See [CLAUDE.md](./CLAUDE.md) for comprehensive architecture documentation including SSE event flow, extraction pipeline details, validation stages, and form filling logic.
