"""固定发布链接：/u/{slug} 始终指向会话当前发布的那一版。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.auth import get_client_ip, get_optional_actor, resolve_actor
from app.core.database import AsyncSessionLocal
from app.models.entities import Conversation, Page, PagePublication, PageVersion
from app.services.analytics import record_page_view
from app.services.analytics.tracking import is_preview_bot, record_analytics_event
from app.services.page_delivery import build_remix_url, build_report_url, render_blocked_page, render_page_html
from app.services.publications import (
    CUSTOM_SUBTITLE_MAX,
    CUSTOM_TITLE_MAX,
    default_subtitle,
    external_view_count,
    is_card_customized,
    publication_url,
    publish_page,
    update_share_card,
)
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
    # 分享卡片当前生效的标题 / 副标题 / 对齐，以及恢复默认时用的默认值。
    title: str
    subtitle: str
    align: Literal["left", "center", "right"]
    default_title: str
    default_subtitle: str
    customized: bool


class ShareCardUpdate(BaseModel):
    """只修改请求里带上的字段；title / subtitle 传 null 或空串表示恢复默认。"""

    title: str | None = Field(default=None, max_length=CUSTOM_TITLE_MAX * 2)
    subtitle: str | None = Field(default=None, max_length=CUSTOM_SUBTITLE_MAX * 2)
    align: Literal["left", "center", "right"] | None = None


def _publish_response(publication: PagePublication, *, republished: bool, views: int, default_title: str) -> PublishResponse:
    base = publication_url(publication.slug)
    return PublishResponse(
        slug=publication.slug,
        url=base,
        qr_url=f"{base}/qr.png",
        poster_url=f"{base}/poster.png",
        og_image_url=f"{base}/og.png",
        republished=republished,
        external_view_count=views,
        title=publication.title,
        subtitle=publication.og_description or default_subtitle(publication.scene_key),
        align=publication.card_align if publication.card_align in ("left", "center", "right") else "center",
        default_title=default_title,
        default_subtitle=default_subtitle(publication.scene_key),
        customized=is_card_customized(publication),
    )


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
            result = await publish_page(session, page=page, preferred_slug=payload.slug)
            await session.refresh(result.publication)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        publication = result.publication
        views = await external_view_count(session, publication.conversation_id)
        slug = publication.slug
        scene_key = publication.scene_key
        owner_id = publication.owner_user_id
        response = _publish_response(publication, republished=result.republished, views=views, default_title=result.default_title)

    await record_analytics_event(
        event_name="scene_publish",
        user_id=owner_id,
        anon_device_id=None,
        client_session_id=None,
        props={"scene_key": scene_key, "slug": slug, "republished": result.republished},
        ip=get_client_ip(request),
        referer=request.headers.get("referer"),
        user_agent=request.headers.get("user-agent"),
    )
    return response


@router.patch("/api/publications/{slug}", response_model=PublishResponse)
async def update_publication_card(slug: str, payload: ShareCardUpdate, request: Request, response_obj: Response) -> PublishResponse:
    """分享浮层里改写标题 / 副标题 / 对齐：保存后重画朋友圈海报和链接卡片，/u/{slug} 的标题与描述随之更新。"""
    async with AsyncSessionLocal() as session:
        user = await resolve_actor(session, request, response_obj)
        if user.is_anonymous:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "need_login", "message": "登录后才能修改分享卡片", "need_login": True},
            )
        publication = await session.scalar(select(PagePublication).where(PagePublication.slug == slug))
        if publication is None or publication.owner_user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分享链接不存在")
        changes = {key: getattr(payload, key) for key in payload.model_fields_set}
        default_title = await update_share_card(session, publication, changes)
        await session.refresh(publication)
        views = await external_view_count(session, publication.conversation_id)
        return _publish_response(publication, republished=False, views=views, default_title=default_title)


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
        if page.moderation_status == "blocked":
            return render_blocked_page()
        version = await session.get(PageVersion, page.current_version_id)
        if version is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="页面版本不存在")
        storage_key = version.storage_key
        title = publication.title
        description = publication.og_description or title
        custom_title = bool(publication.custom_title)
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
    return await render_page_html(
        html,
        title=title,
        description=description,
        image_url=f"{url}/og.png",
        page_url=url,
        print_after_load=print_mode == 1,
        remix_url=build_remix_url(scene.key if scene else None, slug),
        poster_url=f"{url}/poster.png",
        report_url=build_report_url(str(page_id), slug),
        force_title=custom_title,
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

    png = await asyncio.to_thread(render_qr_png, publication_url(publication.slug))
    return Response(content=png, media_type="image/png")


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
