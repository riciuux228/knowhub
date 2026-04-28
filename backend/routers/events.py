import logging
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from backend.config import events

logger = logging.getLogger("knowhub")

router = APIRouter()

@router.get("/api/events")
async def event_stream():
    return StreamingResponse(events.subscribe(), media_type="text/event-stream")
