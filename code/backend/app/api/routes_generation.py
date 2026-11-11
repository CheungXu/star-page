from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from starlette.datastructures import FormData, UploadFile

from app.core.auth import get_client_ip, get_optional_actor, resolve_actor
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.entities import GenerationTask, PageAsset
from app.schemas.conversations import ConversationForkSource
from app.schemas.generation import GenerationCreateRequest, GenerationCreateResponse, GenerationRunItem
from app.services.billing.errors import AnonLimitError, BillingError, InsufficientCreditsError, ModelNotAllowedError
from app.services.consent_service import training_consent_for
from app.services.document_extractor import prepare_generation_input
from app.services.generation_events import HEARTBEAT, stream_task_events
from app.services.generation_runner import CANCELLED_ERROR_MESSAGE, generation_runner, user_concurrency_limit
from app.services.generation_service import CANCELLED_USER_MESSAGE, TERMINAL_STATUSES, GenerationService
from app.services.image_assets import discard_stored_images, store_uploaded_images
from app.services.scenes.catalog import compose_scene_prompt, format_image_block, get_scene, normalize_utm_source, parse_guide
from app.services.sse import SseEvent, format_sse, format_sse_comment

router = APIRouter(prefix="/api/generations", tags=["generations"])


@dataclass
class ParsedGenerationRequest:
    prompt: str
    files: list[UploadFile] = field(default_factory=list)
    images: list[UploadFile] = field(default_factory=list)
    image_roles: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    conversation_id: uuid.UUID | None = None
    base_page_id: uuid.UUID | None = None
    scene_key: str | None = None
    style_key: str | None = None
    guide: dict[str, str] = field(default_factory=dict)
    reveal_contact: bool = False
    utm_source: str | None = None


@router.post("", response_model=GenerationCreateResponse)
async def create_generation(request: Request, response: Response) -> GenerationCreateResponse:
    parsed = await _parse_create_generation_request(request)
    scene = get_scene(parsed.scene_key) if parsed.scene_key else None
    if parsed.scene_key and scene is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="未知场景")

    async with AsyncSessionLocal() as session:
        user = await resolve_actor(session, request, response)

        composed_prompt = parsed.prompt
        if scene is not None and parsed.conversation_id is None:
            composed_prompt = compose_scene_prompt(
                scene,
                parsed.prompt,
                parsed.guide,
                parsed.style_key,
                reveal_contact=parsed.reveal_contact,
            )
        if len(composed_prompt.strip()) < 3:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="prompt 不能为空")

        generation_input = await prepare_generation_input(composed_prompt, parsed.files)
        model_prompt = generation_input.model_prompt
        stored_images = []
        if parsed.images:
            slot_labels = {slot.key: slot.label for slot in scene.image_slots} if scene else {}
            stored_images = await store_uploaded_images(parsed.images, parsed.image_roles, slot_labels)
            labels = []
            base = get_settings().public_base_url.rstrip("/")
            for image in stored_images:
                label = slot_labels.get(image.role, "图片")
                labels.append((label, f"{base}/assets/{image.asset_id}", image.filename))
            image_block = format_image_block(labels)
            if image_block:
                model_prompt = f"{model_prompt}\n\n{image_block}"

        service = GenerationService(session)
        owner_id = user.id
        asset_consent = training_consent_for(user)
        created_ok = False
        try:
            creation = await service.create_batch(
                prompt=model_prompt,
                selected_model_keys=parsed.models,
                title_prompt=composed_prompt,
                user_prompt=generation_input.user_prompt,
                input_file_names=generation_input.input_file_names,
                extracted_file_text=generation_input.extracted_file_text,
                compression_prompt=generation_input.compression_prompt,
                conversation_id=parsed.conversation_id,
                base_page_id=parsed.base_page_id,
                scene_key=scene.key if scene else None,
                utm_source=parsed.utm_source,
                user=user,
                client_ip=get_client_ip(request),
            )
            if stored_images:
                for image in stored_images:
                    session.add(
                        PageAsset(
                            id=image.asset_id,
                            owner_user_id=owner_id,
                            batch_id=creation.batch_id,
                            storage_key=image.storage_key,
                            content_type=image.content_type,
                            filename=image.filename,
                            role=image.role,
                            byte_size=image.byte_size,
                            training_consent=asset_consent,
                        )
                    )
                await session.commit()
            created_ok = True
        except InsufficientCreditsError as exc:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={"code": exc.code, "message": exc.message},
            ) from exc
        except (AnonLimitError, ModelNotAllowedError) as exc:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={"code": exc.code, "message": exc.message, "need_login": True},
            ) from exc
        except BillingError as exc:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={"code": exc.code, "message": exc.message},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        finally:
            if stored_images and not created_ok:
                await discard_stored_images(stored_images)

        # 批次和图片都已提交，交给后台跑；浏览器之后只订阅事件，断开也不影响生成。
        generation_runner.start(
            [run.task_id for run in creation.runs],
            creation.batch_id,
            user_id=owner_id,
            user_limit=user_concurrency_limit(user),
        )

        return GenerationCreateResponse(
            conversation_id=creation.conversation_id,
            batch_id=creation.batch_id,
            kind=creation.kind,
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
            forked_from=ConversationForkSource(**asdict(creation.forked_from)) if creation.forked_from else None,
        )


async def _parse_create_generation_request(request: Request) -> ParsedGenerationRequest:
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        raw_prompt = form.get("prompt")
        if not isinstance(raw_prompt, str):
            raw_prompt = ""

        scene_key = _parse_optional_str(form.get("scene_key"))
        guide = parse_guide(_parse_optional_str(form.get("guide_json")))
        payload = _validate_generation_payload(
            prompt=raw_prompt,
            models=_parse_list_from_form(form, "models"),
            conversation_id=_parse_optional_str(form.get("conversation_id")),
            base_page_id=_parse_optional_str(form.get("base_page_id")),
        )
        files = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
        images = [item for item in form.getlist("images") if isinstance(item, UploadFile)]
        return ParsedGenerationRequest(
            prompt=payload.prompt,
            files=files,
            images=images,
            image_roles=_parse_list_from_form(form, "image_roles"),
            models=payload.models,
            conversation_id=payload.conversation_id,
            base_page_id=payload.base_page_id,
            scene_key=scene_key,
            style_key=_parse_optional_str(form.get("style_key")),
            guide=guide,
            reveal_contact=_is_truthy(form.get("reveal_contact")),
            utm_source=normalize_utm_source(_parse_optional_str(form.get("utm_source"))),
        )

    try:
        payload = GenerationCreateRequest.model_validate(await request.json())
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请求体不是合法 JSON") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc
    return ParsedGenerationRequest(
        prompt=payload.prompt,
        files=[],
        models=payload.models,
        conversation_id=payload.conversation_id,
        base_page_id=payload.base_page_id,
        scene_key=payload.scene_key,
        style_key=payload.style_key,
        guide=parse_guide(json.dumps(payload.guide, ensure_ascii=False)) if payload.guide else {},
        reveal_contact=payload.reveal_contact,
        utm_source=normalize_utm_source(payload.utm_source),
    )


def _validate_generation_payload(
    *,
    prompt: str,
    models: list[str],
    conversation_id: str | None,
    base_page_id: str | None,
) -> GenerationCreateRequest:
    try:
        return GenerationCreateRequest(
            prompt=prompt,
            models=models,
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


def _is_truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


# no-transform：不让中间层（Next 开发代理、CDN 等）压缩事件流，压缩会把小块实时事件攒在缓冲里不发。
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@dataclass(frozen=True)
class _BatchTaskRef:
    task_id: uuid.UUID
    status: str
    requested_by_user_id: uuid.UUID


async def _load_owned_batch_tasks(request: Request, batch_id: uuid.UUID) -> list[_BatchTaskRef]:
    """读批次里的任务并校验归属。不存在或不属于当前操作者都按 404 处理，不暴露批次是否存在。"""
    async with AsyncSessionLocal() as session:
        user = await get_optional_actor(session, request)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
        result = await session.execute(
            select(GenerationTask.id, GenerationTask.status, GenerationTask.requested_by_user_id).where(
                GenerationTask.batch_id == batch_id
            )
        )
        tasks = [_BatchTaskRef(row.id, row.status, row.requested_by_user_id) for row in result.all()]
    if not tasks or any(task.requested_by_user_id != user.id for task in tasks):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="生成批次不存在")
    return tasks


def _event_stream_response(request: Request, events) -> StreamingResponse:
    async def event_generator():
        async for item in events:
            if await request.is_disconnected():
                break
            yield format_sse_comment("ping") if item is HEARTBEAT else format_sse(item)

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/batches/{batch_id}/events")
async def stream_batch_events(batch_id: uuid.UUID, request: Request, after: str | None = None) -> StreamingResponse:
    """一轮一条连接：带这一轮所有模型的事件（payload 里有 task_id），全部收尾后发 batch_done。

    断线重连时浏览器自动带 Last-Event-ID，从断点续传；也可以用 ?after= 显式指定。
    """
    tasks = await _load_owned_batch_tasks(request, batch_id)
    last_event_id = request.headers.get("last-event-id") or after
    events = stream_task_events(
        session_factory=AsyncSessionLocal,
        runner=generation_runner,
        task_ids=[task.task_id for task in tasks],
        signal_key=batch_id,
        last_event_id=last_event_id,
    )
    return _event_stream_response(request, events)


@router.post("/batches/{batch_id}/cancel")
async def cancel_batch(batch_id: uuid.UUID, request: Request) -> dict[str, object]:
    """停止这一轮里还没完成的模型。已完成的照常结算，被停的不扣积分。"""
    tasks = await _load_owned_batch_tasks(request, batch_id)
    stopping = 0
    for task in tasks:
        if task.status in TERMINAL_STATUSES:
            continue
        if await generation_runner.cancel(task.task_id):
            stopping += 1
            continue
        # 不在本进程里调用模型（还在排队或是孤儿任务）：直接写成已停止。
        async with AsyncSessionLocal() as session:
            changed = await GenerationService(session).finish_unfinished_task(
                task.task_id,
                status="cancelled",
                message=CANCELLED_ERROR_MESSAGE,
                user_message=CANCELLED_USER_MESSAGE,
            )
        if changed:
            stopping += 1
            generation_runner.notify(batch_id)
    return {"batch_id": str(batch_id), "stopping": stopping}


@router.get("/{task_id}/events")
async def stream_generation_events(task_id: uuid.UUID, request: Request) -> StreamingResponse:
    """单个任务的事件流（旧前端在用）。只读回放加实时跟进，不会触发或打断生成。"""
    async with AsyncSessionLocal() as session:
        user = await get_optional_actor(session, request)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
        task = await session.get(GenerationTask, task_id)

    async def single(message: str):
        yield SseEvent("failed", {"type": "failed", "message": message})

    if task is None:
        return _event_stream_response(request, single("生成任务不存在"))
    if task.requested_by_user_id != user.id:
        return _event_stream_response(request, single("无权访问该生成任务"))

    if task.status == "pending" and not generation_runner.is_active(task.id):
        # 兜底：没人在跑的 pending 任务交给后台；正常情况下创建批次时就已经启动了。
        generation_runner.start(
            [task.id], task.batch_id, user_id=user.id, user_limit=user_concurrency_limit(user)
        )

    events = stream_task_events(
        session_factory=AsyncSessionLocal,
        runner=generation_runner,
        task_ids=[task.id],
        signal_key=task.batch_id or task.id,
        last_event_id=request.headers.get("last-event-id"),
    )
    return _event_stream_response(request, events)
