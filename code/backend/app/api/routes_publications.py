"""固定发布链接：/u/{slug} 始终指向会话当前发布的那一版。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.auth import get_client_ip, get_optional_actor, resolve_actor
from app.core.database import AsyncSessionLocal
from app.models.entities import Conversation, Page, PagePublication, PageVersion
from app.services.analytics import record_page_view
from app.services.analytics.tracking import is_preview_bot, record_analytics_event
from app.services.page_delivery import build_remix_url, render_page_html
from app.services.publications import external_view_count, publication_url, publish_page
from app.services.scenes.catalog import get_scene
from app.services.storage.factory import create_storage_provider

router = APIRouter(tags=["publications"])


class PublishRequest(BaseModel):
    page_id: uuid.UUID
    slug: str | None = Field(default=None, max_length=64)


class PublishResponse(BaseModel):
    slug: str
    url: str
    qr_url: str
    poster_url: str
    og_image_url: str
    republished: bool
    external_view_count: int


@router.post("/api/publications", response_model=PublishResponse)
async def create_publication(payload: PublishRequest, request: Request, response_obj: Response) -> PublishResponse:
    async with AsyncSessionLocal() as session:
        user = await resolve_actor(session, request, response_obj)
        if user.is_anonymous:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "need_login", "message": "登录后才能发布固定链接", "need_login": True},
            )
        page = await session.get(Page, payload.page_id)
        if page is None or page.deleted_at is not None or page.owner_user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="页面不存在")
        try:
            publication, republished = await publish_page(session, page=page, preferred_slug=payload.slug)
            await session.refresh(publication)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        views = await external_view_count(session, publication.conversation_id)
        slug = publication.slug
        scene_key = publication.scene_key
        owner_id = publication.owner_user_id

    await record_analytics_event(
        event_name="scene_publish",
        user_id=owner_id,
        anon_device_id=None,
        client_session_id=None,
        props={"scene_key": scene_key, "slug": slug, "republished": republished},
        ip=get_client_ip(request),
        referer=request.headers.get("referer"),
        user_agent=request.headers.get("user-agent"),
    )
    base = publication_url(slug)
    return PublishResponse(
        slug=slug,
        url=base,
        qr_url=f"{base}/qr.png",
        poster_url=f"{base}/poster.png",
        og_image_url=f"{base}/og.png",
        republished=republished,
        external_view_count=views,
    )


@router.get("/u/{slug}")
async def serve_publication(
    slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    print_mode: int = Query(default=0, alias="print"),
) -> HTMLResponse:
    async with AsyncSessionLocal() as session:
        publication = await session.scalar(select(PagePublication).where(PagePublication.slug == slug))
        if publication is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="页面不存在")
        page = await session.get(Page, publication.page_id)
        conversation = await session.get(Conversation, publication.conversation_id)
        if (
            page is None
            or page.deleted_at is not None
            or page.status != "ready"
            or page.current_version_id is None
            or conversation is None
            or conversation.deleted_at is not None
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="页面不存在")
        version = await session.get(PageVersion, page.current_version_id)
        if version is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="页面版本不存在")
        storage_key = version.storage_key
        title = publication.title
        description = publication.og_description or title
        page_id = page.id
        conversation_id = page.conversation_id
        owner_user_id = page.owner_user_id
        user = await get_optional_actor(session, request)
        viewer_user_id = user.id if user is not None else None
        is_owner_view = bool(user is not None and user.id == owner_user_id)
        scene = get_scene(publication.scene_key)

    storage = create_storage_provider()
    html = await storage.get_text(storage_key)
    url = publication_url(slug)
    if not is_preview_bot(request.headers.get("user-agent")):
        background_tasks.add_task(
            record_page_view,
            page_id=page_id,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            viewer_user_id=viewer_user_id,
            is_owner_view=is_owner_view,
            ip=get_client_ip(request),
            referer=request.headers.get("referer"),
            user_agent=request.headers.get("user-agent"),
        )
    if scene and scene.tagline and not description:
        description = scene.tagline
    return render_page_html(
        html,
        title=title,
        description=description,
        image_url=f"{url}/og.png",
        page_url=url,
        print_after_load=print_mode == 1,
        remix_url=build_remix_url(scene.key if scene else None, slug),
        poster_url=f"{url}/poster.png",
    )


@router.get("/u/{slug}/og.png")
async def publication_og(slug: str) -> Response:
    return await _share_image(slug, kind="og")


@router.get("/u/{slug}/poster.png")
async def publication_poster(slug: str) -> Response:
    return await _share_image(slug, kind="poster")


@router.get("/u/{slug}/qr.png")
async def publication_qr(slug: str) -> Response:
    publication = await _load_publication(slug)
    from app.services.share_card import render_qr_png

    return Response(content=render_qr_png(publication_url(publication.slug)), media_type="image/png")


async def _share_image(slug: str, *, kind: str) -> Response:
    publication = await _load_publication(slug)
    key = publication.og_image_key if kind == "og" else publication.poster_image_key
    if not key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="图片不存在")
    storage = create_storage_provider()
    try:
        data = await storage.get_bytes(key)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="图片不存在") from exc
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "public, max-age=300"})


async def _load_publication(slug: str) -> PagePublication:
    async with AsyncSessionLocal() as session:
        publication = await session.scalar(select(PagePublication).where(PagePublication.slug == slug))
        if publication is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="页面不存在")
        conversation = await session.get(Conversation, publication.conversation_id)
        page = await session.get(Page, publication.page_id)
        if (
            conversation is None
            or conversation.deleted_at is not None
            or page is None
            or page.deleted_at is not None
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="页面不存在")
        session.expunge(publication)
        return publication
