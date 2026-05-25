from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.schemas.generation import GenerationCreateRequest, GenerationCreateResponse
from app.services.document_extractor import prepare_generation_input
from app.services.generation_service import GenerationService
from app.services.sse import format_sse

router = APIRouter(prefix="/api/generations", tags=["generations"])


@router.post("", response_model=GenerationCreateResponse)
async def create_generation(request: Request) -> GenerationCreateResponse:
    prompt, files = await _parse_create_generation_request(request)
    generation_input = await prepare_generation_input(prompt, files)

    async with AsyncSessionLocal() as session:
        service = GenerationService(session)
        task, page = await service.create_generation(
            generation_input.model_prompt,
            title_prompt=prompt,
            user_prompt=generation_input.user_prompt,
            input_file_names=generation_input.input_file_names,
            extracted_file_text=generation_input.extracted_file_text,
            compression_prompt=generation_input.compression_prompt,
        )
        page_url = f"{get_settings().public_base_url.rstrip('/')}/p/{page.id}"
        return GenerationCreateResponse(
            task_id=task.id,
            page_id=page.id,
            status=task.status,
            page_url=page_url,
        )


async def _parse_create_generation_request(request: Request) -> tuple[str, list[UploadFile]]:
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        raw_prompt = form.get("prompt")
        if not isinstance(raw_prompt, str):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="prompt 不能为空")

        payload = _validate_generation_payload(raw_prompt)
        files = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
        return payload.prompt, files

    try:
        payload = GenerationCreateRequest.model_validate(await request.json())
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc
    return payload.prompt, []


def _validate_generation_payload(prompt: str) -> GenerationCreateRequest:
    try:
        return GenerationCreateRequest(prompt=prompt)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc


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
