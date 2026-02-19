"""FastAPI entry point for the PA Form Auto-Fill application.

Run with: uvicorn api.index:app --reload --port 8000
(or just: uvicorn api:app --reload --port 8000)

Set SKIP_MODEL=1 to skip MedGemma loading (uses pre-computed results only).

Vercel will automatically route requests starting with /api to this module.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # Load .env into os.environ before anything else

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("PA Form Auto-Fill API starting...")

    # Load MedGemma model at startup (unless SKIP_MODEL=1)
    if not os.environ.get("SKIP_MODEL"):
        try:
            from extraction_service import ExtractionService
            svc = ExtractionService.get_instance()
            svc.initialize()
        except Exception as e:
            print(f"WARNING: Failed to load MedGemma model: {e}")
            print("Live extraction will be unavailable. Pre-computed results still work.")
    else:
        print("SKIP_MODEL=1 — using pre-computed extraction results only.")

    yield
    print("Shutting down...")


app = FastAPI(
    title="PA Form Auto-Fill",
    description="Prior Authorization form auto-filling with MedGemma + FHIR",
    lifespan=lifespan,
)

# CORS for Vue dev server and production
_allowed_origins = ["http://localhost:5173", "http://localhost:3000"]
_prod_url = os.environ.get("FRONTEND_URL", "")
if _prod_url:
    _allowed_origins.append(_prod_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
from api.patients import router as patients_router
from api.extraction import router as extraction_router
from api.form import router as form_router

app.include_router(patients_router, prefix="/api")
app.include_router(extraction_router, prefix="/api")
app.include_router(form_router, prefix="/api")


@app.get("/api/health")
def health():
    from extraction_service import ExtractionService
    svc = ExtractionService.get_instance()
    return {
        "status": "ok",
        "model_loaded": svc.initialized,
    }


# Serve built Vue frontend (production mode)
_dist = Path("frontend/dist")
if _dist.exists():
    _assets = _dist / "assets"
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        # Serve specific files from dist root (favicon, etc.)
        file_path = _dist / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_dist / "index.html"))
