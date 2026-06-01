from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import FormData, UploadFile

from app.core.database import AsyncSessionLocal
from app.schemas.generation import GenerationCreateRequest, GenerationCreateResponse, GenerationRunItem
from app.services.document_extractor import prepare_generation_input
from app.services.generation_service import GenerationService
from app.services.sse import format_sse

router = APIRouter(prefix="/api/generations", tags=["generations"])


@dataclass
class ParsedGenerationRequest:
    prompt: str
    files: list[UploadFile] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    skill_keys: list[str] = field(default_factory=list)
    conversation_id: uuid.UUID | None = None
    base_page_id: uuid.UUID | None = None


@router.post("", response_model=GenerationCreateResponse)
async def create_generation(request: Request) -> GenerationCreateResponse:
    parsed = await _parse_create_generation_request(request)
    generation_input = await prepare_generation_input(parsed.prompt, parsed.files)

    async with AsyncSessionLocal() as session:
        service = GenerationService(session)
        try:
            creation = await service.create_batch(
                prompt=generation_input.model_prompt,
                selected_model_keys=parsed.models,
                title_prompt=parsed.prompt,
                user_prompt=generation_input.user_prompt,
                input_file_names=generation_input.input_file_names,
                extracted_file_text=generation_input.extracted_file_text,
                compression_prompt=generation_input.compression_prompt,
                conversation_id=parsed.conversation_id,
                base_page_id=parsed.base_page_id,
                skill_keys=parsed.skill_keys,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

        return GenerationCreateResponse(
            conversation_id=creation.conversation_id,
            batch_id=creation.batch_id,
            kind=creation.kind,
            skill_key=creation.skill_key,
            skill_name=creation.skill_name,
            runs=[
                GenerationRunItem(
                    task_id=run.task_id,
                    page_id=run.page_id,
                    model_key=run.model_key,
                    model_label=run.model_label,
                    page_url=run.page_url,
                    status="pending",
                )
                for run in creation.runs
            ],
        )


async def _parse_create_generation_request(request: Request) -> ParsedGenerationRequest:
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        raw_prompt = form.get("prompt")
        if not isinstance(raw_prompt, str):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="prompt 不能为空")

        payload = _validate_generation_payload(
            prompt=raw_prompt,
            models=_parse_list_from_form(form, "models"),
            skill_keys=_parse_list_from_form(form, "skill_keys"),
            conversation_id=_parse_optional_str(form.get("conversation_id")),
            base_page_id=_parse_optional_str(form.get("base_page_id")),
        )
        files = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
        return ParsedGenerationRequest(
            prompt=payload.prompt,
            files=files,
            models=payload.models,
            skill_keys=payload.skill_keys,
            conversation_id=payload.conversation_id,
            base_page_id=payload.base_page_id,
        )

    try:
        payload = GenerationCreateRequest.model_validate(await request.json())
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc
    return ParsedGenerationRequest(
        prompt=payload.prompt,
        files=[],
        models=payload.models,
        skill_keys=payload.skill_keys,
        conversation_id=payload.conversation_id,
        base_page_id=payload.base_page_id,
    )


def _validate_generation_payload(
    *,
    prompt: str,
    models: list[str],
    skill_keys: list[str],
    conversation_id: str | None,
    base_page_id: str | None,
) -> GenerationCreateRequest:
    try:
        return GenerationCreateRequest(
            prompt=prompt,
            models=models,
            skill_keys=skill_keys,
            conversation_id=conversation_id,
            base_page_id=base_page_id,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc


def _parse_list_from_form(form: FormData, field_name: str) -> list[str]:
    raw_values = [value for value in form.getlist(field_name) if isinstance(value, str)]
    # 支持两种传法：重复字段 field=a&field=b，或单个 JSON 数组字符串。
    if len(raw_values) == 1 and raw_values[0].strip().startswith("["):
        try:
            parsed = json.loads(raw_values[0])
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            return []
    return [value.strip() for value in raw_values if value.strip()]


def _parse_optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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
