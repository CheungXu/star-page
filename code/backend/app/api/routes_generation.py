from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.schemas.generation import GenerationCreateRequest, GenerationCreateResponse
from app.services.generation_service import GenerationService
from app.services.sse import format_sse

router = APIRouter(prefix="/api/generations", tags=["generations"])


@router.post("", response_model=GenerationCreateResponse)
async def create_generation(payload: GenerationCreateRequest) -> GenerationCreateResponse:
    async with AsyncSessionLocal() as session:
        service = GenerationService(session)
        task, page = await service.create_generation(payload.prompt)
        page_url = f"{get_settings().public_base_url.rstrip('/')}/p/{page.id}"
        return GenerationCreateResponse(
            task_id=task.id,
            page_id=page.id,
            status=task.status,
            page_url=page_url,
        )


@router.get("/{task_id}/events")
async def stream_generation_events(task_id: uuid.UUID, request: Request) -> StreamingResponse:
    async def event_generator():
        async with AsyncSessionLocal() as session:
            service = GenerationService(session)
            async for event in service.run_or_replay(task_id):
                if await request.is_disconnected():
                    break
                yield format_sse(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
