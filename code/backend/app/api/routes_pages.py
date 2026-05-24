from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.default_user import ensure_default_user
from app.models.entities import Page, PageVersion
from app.schemas.pages import PageResponse
from app.services.permission_service import can_view_page
from app.services.storage.factory import create_storage_provider

router = APIRouter(tags=["pages"])

CSP_HEADER = "; ".join(
    [
        "default-src 'none'",
        "img-src https: data:",
        "style-src 'unsafe-inline'",
        "font-src https: data:",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'self'",
        "script-src 'none'",
    ]
)


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
            url=f"{get_settings().public_base_url.rstrip('/')}/p/{page.id}",
            created_at=page.created_at,
            updated_at=page.updated_at,
        )


@router.get("/p/{page_id}")
async def serve_page(page_id: uuid.UUID) -> HTMLResponse:
    async with AsyncSessionLocal() as session:
        page = await session.get(Page, page_id)
        if page is None or page.deleted_at is not None:
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
            "Content-Security-Policy": CSP_HEADER,
            "X-Content-Type-Options": "nosniff",
        },
    )
