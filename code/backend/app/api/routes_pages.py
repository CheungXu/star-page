from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, or_, select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.default_user import ensure_default_user
from app.core.urls import build_page_url
from app.models.entities import Conversation, GenerationTask, Page, PagePermission, PageVersion
from app.schemas.pages import PageHistoryItem, PageResponse
from app.services.permission_service import can_view_page
from app.services.storage.factory import create_storage_provider

router = APIRouter(tags=["pages"])


def _build_page_csp() -> str:
    """生成页统一的展示型 CSP：sandbox 把页面关进不透明 origin（碰不到主站凭证），
    connect-src 'none' 禁止一切对外网络（防钓鱼/信标），脚本/样式放行内联与可信 CDN。"""
    cdn = " ".join(get_settings().generated_page_cdn_sources)
    script_src = ("'unsafe-inline' " + cdn).strip()
    style_src = ("'unsafe-inline' " + cdn).strip()
    return "; ".join(
        [
            "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox",
            "default-src 'none'",
            f"script-src {script_src}",
            f"style-src {style_src}",
            "img-src https: data:",
            "font-src https: data:",
            "media-src https: data:",
            "connect-src 'none'",
            "form-action 'none'",
            "base-uri 'none'",
            "frame-ancestors 'self'",
        ]
    )


@router.get("/api/pages", response_model=list[PageHistoryItem])
async def list_pages() -> list[PageHistoryItem]:
    settings = get_settings()

    async with AsyncSessionLocal() as session:
        user = await ensure_default_user(session)
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
async def get_page(page_id: uuid.UUID) -> PageResponse:
    async with AsyncSessionLocal() as session:
        page = await session.get(Page, page_id)
        if page is None:
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


@router.get("/p/{conversation_id}/{page_id}")
async def serve_page(conversation_id: uuid.UUID, page_id: uuid.UUID) -> HTMLResponse:
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

        user = await ensure_default_user(session)
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

    storage = create_storage_provider()
    html = await storage.get_text(version.storage_key)
    return HTMLResponse(
        content=html,
        headers={
            "Content-Security-Policy": _build_page_csp(),
            "X-Content-Type-Options": "nosniff",
        },
    )
