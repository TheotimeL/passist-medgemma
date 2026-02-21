"""FastAPI entry point for the PA Form Auto-Fill application.

Run with: uvicorn server:app --reload --port 8000

Set EXTRACTION_BACKEND=gemini to use Gemini instead of local MedGemma.
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

from dotenv import load_dotenv

load_dotenv()  # Load .env into os.environ before anything else

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("PA Form Auto-Fill API starting...")

    # Load extraction service at startup
    # SKIP_MODEL only skips the heavy MLX model; Gemini backend is lightweight
    skip = os.environ.get("SKIP_MODEL")
    backend = os.environ.get("EXTRACTION_BACKEND") or "mlx"
    if not skip or backend != "mlx":
        try:
            from extraction_service import ExtractionService
            svc = ExtractionService.get_instance()
            svc.initialize()
        except Exception as e:
            logger.warning("Failed to initialize extraction service: %s", e)
            logger.warning("Live extraction will be unavailable.")

        # 4B drug field parser loads lazily on first use (after 27B finishes)
        # to avoid Metal GPU conflicts during startup
    else:
        logger.info("SKIP_MODEL=1 — extraction will be unavailable.")

    yield
    logger.info("Shutting down...")


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
from api.pas_bundle import router as pas_bundle_router

app.include_router(patients_router, prefix="/api")
app.include_router(extraction_router, prefix="/api")
app.include_router(form_router, prefix="/api")
app.include_router(pas_bundle_router, prefix="/api")


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
