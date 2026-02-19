"""MedGemma extraction endpoint with SSE streaming.

Supports two modes:
1. Pre-computed results from extraction_*.json files (GET /patients/{uuid}/extraction)
2. Live SSE streaming from ExtractionService (GET /patients/{uuid}/extract)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

router = APIRouter()

EXTRACTIONS_DIR = Path(".")


def _find_extraction(uuid: str) -> dict | None:
    """Find pre-computed extraction results for a patient UUID."""
    for json_file in EXTRACTIONS_DIR.glob("extraction_*.json"):
        try:
            data = json.loads(json_file.read_text())
            if isinstance(data, list):
                for entry in data:
                    if entry.get("uuid", "").startswith(uuid[:8]):
                        return entry
            elif isinstance(data, dict) and data.get("uuid", "").startswith(uuid[:8]):
                return data
        except (json.JSONDecodeError, KeyError):
            continue
    return None


@router.get("/patients/{uuid}/extraction")
def get_extraction(uuid: str):
    """Return pre-computed extraction results."""
    result = _find_extraction(uuid)
    if not result:
        raise HTTPException(status_code=404, detail="No extraction results found. Run extraction first.")
    return JSONResponse(content=result)


@router.get("/patients/{uuid}/extract")
async def extract_live(uuid: str):
    """SSE endpoint for live MedGemma extraction.

    Streams events progressively as the extraction runs:
    - event: status  → progress messages
    - event: run     → partial results after each inference run
    - event: policy  → policy evaluation result
    - event: complete → final extraction results
    - event: error   → error messages
    """
    from extraction_service import ExtractionService

    svc = ExtractionService.get_instance()
    if not svc.initialized:
        raise HTTPException(status_code=503, detail="Model still loading. Try again shortly.")

    async def event_stream():
        # Use an asyncio.Queue to bridge sync generator → async SSE stream
        # so events are sent to the client as they're produced (not batched).
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def run_extraction():
            try:
                for event in svc.extract(uuid):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as e:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "data": {"message": str(e)}},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        # Serialize extractions to prevent concurrent GPU usage (Metal conflict)
        async with svc._gpu_lock:
            loop.run_in_executor(None, run_extraction)
            while True:
                event = await queue.get()
                if event is None:
                    break
                event_type = event.get("type", "status")
                event_data = json.dumps(event.get("data", {}))
                yield f"event: {event_type}\ndata: {event_data}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
