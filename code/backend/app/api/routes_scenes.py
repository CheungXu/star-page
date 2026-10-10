"""场景目录、场景案例，以及运营侧的案例标记。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.core.auth import require_admin
from app.core.database import AsyncSessionLocal
from app.core.urls import build_page_url
from app.core.config import get_settings
from app.models.entities import Page, PagePublication, SceneShowcase
from app.services.publications import publication_url
from app.services.scenes.catalog import get_scene, list_scenes

router = APIRouter(tags=["scenes"])


class ShowcaseCreate(BaseModel):
    scene_key: str = Field(min_length=1, max_length=64)
    page_id: uuid.UUID
    title: str = Field(default="", max_length=200)
    summary: str | None = Field(default=None, max_length=500)
    sort_order: int = 0


class ShowcaseItem(BaseModel):
    id: uuid.UUID
    scene_key: str
    page_id: uuid.UUID
    title: str
    summary: str | None
    sort_order: int
    enabled: bool
    page_url: str


@router.get("/api/scenes")
async def list_public_scenes() -> dict[str, list[dict]]:
    return {"scenes": [scene.public_dict() for scene in list_scenes()]}


@router.get("/api/scenes/{scene_key}")
async def get_public_scene(scene_key: str) -> dict:
    scene = get_scene(scene_key)
    if scene is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="场景不存在")
    payload = scene.public_dict()
    payload["cases"] = await _public_cases(scene.key)
    return payload


@router.get("/api/admin/scenes/showcases", response_model=list[ShowcaseItem])
async def list_showcases(request: Request, scene_key: str | None = None) -> list[ShowcaseItem]:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        stmt = select(SceneShowcase, Page).join(Page, Page.id == SceneShowcase.page_id)
        if scene_key:
            stmt = stmt.where(SceneShowcase.scene_key == scene_key)
        stmt = stmt.order_by(SceneShowcase.sort_order.asc(), SceneShowcase.created_at.desc())
        rows = (await session.execute(stmt)).all()
        return [
            ShowcaseItem(
                id=item.id,
                scene_key=item.scene_key,
                page_id=item.page_id,
                title=item.title,
                summary=item.summary,
                sort_order=item.sort_order,
                enabled=item.enabled,
                page_url=build_page_url(settings, page.conversation_id, page.id),
            )
            for item, page in rows
        ]


@router.post("/api/admin/scenes/showcases", response_model=ShowcaseItem)
async def create_showcase(payload: ShowcaseCreate, request: Request) -> ShowcaseItem:
    if get_scene(payload.scene_key) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="未知场景")
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        page = await session.get(Page, payload.page_id)
        if page is None or page.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="页面不存在")
        existing = await session.scalar(
            select(SceneShowcase).where(
                SceneShowcase.scene_key == payload.scene_key,
                SceneShowcase.page_id == page.id,
            )
        )
        title = (payload.title or page.title)[:200]
        if existing is None:
            existing = SceneShowcase(
                scene_key=payload.scene_key,
                page_id=page.id,
                title=title,
                summary=payload.summary,
                sort_order=payload.sort_order,
                enabled=True,
            )
            session.add(existing)
        else:
            existing.title = title
            existing.summary = payload.summary
            existing.sort_order = payload.sort_order
            existing.enabled = True
        await session.commit()
        await session.refresh(existing)
        return ShowcaseItem(
            id=existing.id,
            scene_key=existing.scene_key,
            page_id=existing.page_id,
            title=existing.title,
            summary=existing.summary,
            sort_order=existing.sort_order,
            enabled=existing.enabled,
            page_url=build_page_url(settings, page.conversation_id, page.id),
        )


@router.get("/api/admin/scenes/funnel")
async def scene_funnel(request: Request) -> dict:
    """按场景汇总落地、生成、发布和外部访问。直接查明细，不依赖日聚合任务。"""
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        landing = await _count_map(
            session,
            """
            SELECT props->>'scene_key' AS k, count(*) AS n
            FROM analytics_events
            WHERE event_name = 'scene_landing_view' AND coalesce(props->>'scene_key', '') <> ''
            GROUP BY 1
            """,
        )
        clicks = await _count_map(
            session,
            """
            SELECT props->>'scene_key' AS k, count(*) AS n
            FROM analytics_events
            WHERE event_name = 'generate_click' AND coalesce(props->>'scene_key', '') <> ''
            GROUP BY 1
            """,
        )
        conversations = await _count_map(
            session,
            "SELECT scene_key AS k, count(*) AS n FROM conversations WHERE scene_key IS NOT NULL AND deleted_at IS NULL GROUP BY 1",
        )
        tasks = await _count_map(
            session,
            "SELECT scene_key AS k, count(*) AS n FROM generation_tasks WHERE scene_key IS NOT NULL GROUP BY 1",
        )
        succeeded = await _count_map(
            session,
            "SELECT scene_key AS k, count(*) AS n FROM generation_tasks WHERE scene_key IS NOT NULL AND status = 'succeeded' GROUP BY 1",
        )
        publications = await _count_map(
            session,
            "SELECT scene_key AS k, count(*) AS n FROM page_publications WHERE scene_key IS NOT NULL GROUP BY 1",
        )
        views = await _count_map(
            session,
            """
            SELECT p.scene_key AS k, count(*) AS n
            FROM page_view_events v
            JOIN pages p ON p.id = v.page_id
            WHERE v.is_owner_view = false AND p.scene_key IS NOT NULL
            GROUP BY 1
            """,
        )

    items = []
    for scene in list_scenes():
        items.append(
            {
                "scene_key": scene.key,
                "name": scene.name,
                "priority": scene.priority,
                "landing_views": landing.get(scene.key, 0),
                "generate_clicks": clicks.get(scene.key, 0),
                "conversations": conversations.get(scene.key, 0),
                "tasks": tasks.get(scene.key, 0),
                "tasks_succeeded": succeeded.get(scene.key, 0),
                "publications": publications.get(scene.key, 0),
                "external_views": views.get(scene.key, 0),
            }
        )
    return {"scenes": items}


async def _count_map(session, sql: str) -> dict[str, int]:
    rows = (await session.execute(text(sql))).all()
    return {str(row.k): int(row.n) for row in rows if row.k}


@router.delete("/api/admin/scenes/showcases/{showcase_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_showcase(showcase_id: uuid.UUID, request: Request) -> None:
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        item = await session.get(SceneShowcase, showcase_id)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="案例不存在")
        item.enabled = False
        await session.commit()


async def _public_cases(scene_key: str) -> list[dict]:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(SceneShowcase, Page)
                .join(Page, Page.id == SceneShowcase.page_id)
                .where(
                    SceneShowcase.scene_key == scene_key,
                    SceneShowcase.enabled.is_(True),
                    Page.deleted_at.is_(None),
                    Page.visibility == "public",
                    Page.status == "ready",
                )
                .order_by(SceneShowcase.sort_order.asc(), SceneShowcase.created_at.desc())
                .limit(6)
            )
        ).all()
        cases: list[dict] = []
        scene = get_scene(scene_key)
        for item, page in rows:
            publication = None
            if page.conversation_id is not None:
                publication = await session.scalar(
                    select(PagePublication).where(PagePublication.conversation_id == page.conversation_id)
                )
            page_url = publication_url(publication.slug) if publication is not None else build_page_url(settings, page.conversation_id, page.id)
            cases.append(
                {
                    "showcase_id": str(item.id),
                    "page_id": str(page.id),
                    "title": item.title,
                    "summary": item.summary,
                    "page_url": page_url,
                    # 不返回用户原文，避免简历里的电话和邮箱随着案例公开。
                    "remix_prompt": scene.prompt_template if scene else "",
                }
            )
        return cases
