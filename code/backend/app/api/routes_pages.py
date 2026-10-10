from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func, or_, select

from app.core.auth import get_client_ip, get_optional_actor
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.urls import build_page_url
from app.models.entities import Conversation, GenerationTask, Page, PagePermission, PageVersion, PageViewEvent
from app.schemas.pages import PageHistoryItem, PageResponse
from app.services.analytics import record_page_view
from app.services.analytics.tracking import is_preview_bot
from app.services.page_delivery import build_remix_url, render_page_html
from app.services.permission_service import can_view_page
from app.services.scenes.catalog import get_scene
from app.services.storage.factory import create_storage_provider

router = APIRouter(tags=["pages"])


@router.get("/api/pages", response_model=list[PageHistoryItem])
async def list_pages(request: Request) -> list[PageHistoryItem]:
    settings = get_settings()

    async with AsyncSessionLocal() as session:
        user = await get_optional_actor(session, request)
        if user is None:
            return []
        result = await session.execute(
            select(Page)
            .outerjoin(
                PagePermission,
                (PagePermission.page_id == Page.id)
                & (PagePermission.user_id == user.id)
                & (PagePermission.role.in_(["owner", "viewer", "editor"])),
            )
            .where(
                Page.deleted_at.is_(None),
                or_(Page.owner_user_id == user.id, PagePermission.id.is_not(None)),
            )
            .order_by(desc(Page.updated_at), desc(Page.created_at))
            .limit(50)
        )
        pages = result.scalars().unique().all()

        items: list[PageHistoryItem] = []
        for page in pages:
            task_result = await session.execute(
                select(GenerationTask)
                .where(GenerationTask.page_id == page.id)
                .order_by(desc(GenerationTask.created_at))
                .limit(1)
            )
            task = task_result.scalar_one_or_none()
            prompt = task.user_prompt or task.prompt if task else page.title

            items.append(
                PageHistoryItem(
                    id=page.id,
                    task_id=task.id if task else None,
                    title=page.title,
                    prompt=prompt,
                    file_names=task.input_file_names if task else [],
                    page_url=build_page_url(settings, page.conversation_id, page.id),
                    page_status=page.status,
                    generation_status=task.status if task else None,
                    created_at=page.created_at,
                    updated_at=page.updated_at,
                )
            )

        return items


@router.get("/api/pages/{page_id}", response_model=PageResponse)
async def get_page(page_id: uuid.UUID, request: Request) -> PageResponse:
    async with AsyncSessionLocal() as session:
        user = await get_optional_actor(session, request)
        page = await session.get(Page, page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="页面不存在")
        if not await can_view_page(session, page, user):
            raise HTTPException(status_code=404, detail="页面不存在")

        return PageResponse(
            id=page.id,
            title=page.title,
            visibility=page.visibility,
            status=page.status,
            current_version_id=page.current_version_id,
            url=build_page_url(get_settings(), page.conversation_id, page.id),
            created_at=page.created_at,
            updated_at=page.updated_at,
        )


@router.get("/api/pages/{page_id}/stats")
async def page_stats(page_id: uuid.UUID, request: Request) -> dict[str, int]:
    """作者查看该会话下的外部访问次数。续写换了节点也不清零。"""
    async with AsyncSessionLocal() as session:
        user = await get_optional_actor(session, request)
        page = await session.get(Page, page_id)
        if page is None or page.deleted_at is not None:
            raise HTTPException(status_code=404, detail="页面不存在")
        if user is None or user.id != page.owner_user_id:
            raise HTTPException(status_code=403, detail="无权查看访问数据")
        result = await session.execute(
            select(func.count())
            .select_from(PageViewEvent)
            .where(
                PageViewEvent.conversation_id == page.conversation_id,
                PageViewEvent.is_owner_view.is_(False),
            )
        )
        external = int(result.scalar_one() or 0)
    return {"external_view_count": external}


@router.get("/p/{conversation_id}/{page_id}")
async def serve_page(
    conversation_id: uuid.UUID,
    page_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    print_mode: int = Query(default=0, alias="print"),
):
    async with AsyncSessionLocal() as session:
        page = await session.get(Page, page_id)
        if page is None or page.deleted_at is not None:
            raise HTTPException(status_code=404, detail="页面不存在")

        # 节点必须确实归属于路径中的会话，避免伪造/错配的层级链接。
        if page.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="页面不存在")

        # 所属会话被软删时，节点链接也应失效（删会话已级联软删节点，此处为防御冗余）。
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="页面不存在")

        user = await get_optional_actor(session, request)
        if not await can_view_page(session, page, user):
            raise HTTPException(status_code=403, detail="无权访问该页面")

        if page.status != "ready" or page.current_version_id is None:
            raise HTTPException(status_code=409, detail="页面尚未生成完成")

        result = await session.execute(
            select(PageVersion).where(PageVersion.id == page.current_version_id, PageVersion.page_id == page.id)
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise HTTPException(status_code=404, detail="页面版本不存在")

        # 在会话关闭前取出埋点所需字段（避免懒加载/会话过期）。
        owner_user_id = page.owner_user_id
        viewer_user_id = user.id if user is not None else None
        is_owner_view = bool(user is not None and user.id == owner_user_id)
        page_title = page.title
        scene = get_scene(page.scene_key)

    storage = create_storage_provider()
    html = await storage.get_text(version.storage_key)

    # 访问埋点：放到 BackgroundTask，响应返回后再异步写，绝不阻塞页面访问热路径。
    # 分享卡片爬虫不记，避免「外部访问」在发出去之前就被刷高。
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

    settings = get_settings()
    page_url = build_page_url(settings, conversation_id, page_id)
    description = scene.tagline if scene and scene.tagline else f"{page_title} · 由星页 StarPage 生成"
    return render_page_html(
        html,
        title=page_title,
        description=description,
        image_url=None,
        page_url=page_url,
        print_after_load=print_mode == 1,
        remix_url=build_remix_url(scene.key if scene else None, str(page_id)),
    )
