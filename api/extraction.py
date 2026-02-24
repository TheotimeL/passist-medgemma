"""MedGemma extraction endpoint with SSE streaming."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter()


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
    from services.extraction_service import ExtractionService

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
