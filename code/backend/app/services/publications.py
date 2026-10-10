"""固定发布链接：一个会话一个 slug，重新发布只切换当前页面。"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from bs4 import BeautifulSoup

from app.models.entities import Page, PagePublication, PageVersion, PageViewEvent
from app.services.scenes.catalog import get_scene
from app.services.share_card import render_card_png, render_qr_png
from app.services.storage.factory import create_storage_provider

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,47}$")


def normalize_slug(preferred: str | None) -> str | None:
    if not preferred:
        return None
    raw = preferred.strip().lower()
    if _SLUG_RE.fullmatch(raw):
        return raw
    return None


def allocate_slug(preferred: str | None) -> str:
    return normalize_slug(preferred) or f"p-{uuid.uuid4().hex[:10]}"


def publication_url(slug: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/u/{slug}"


async def publish_page(
    session: AsyncSession,
    *,
    page: Page,
    preferred_slug: str | None,
) -> tuple[PagePublication, bool]:
    """发布或重新发布。返回 (记录, 是否为重新发布)。"""
    if page.conversation_id is None:
        raise ValueError("页面还没有会话，无法发布固定链接")
    if page.status != "ready":
        raise ValueError("页面尚未生成完成")

    existing = await session.scalar(select(PagePublication).where(PagePublication.conversation_id == page.conversation_id))
    scene = get_scene(page.scene_key)
    title = await _page_display_title(session, page)
    description = scene.tagline if scene and scene.tagline else f"{title} · 由星页 StarPage 生成"
    republished = existing is not None

    if existing is None:
        slug = await _unique_slug(session, allocate_slug(preferred_slug))
        publication = PagePublication(
            owner_user_id=page.owner_user_id,
            conversation_id=page.conversation_id,
            page_id=page.id,
            slug=slug,
            scene_key=page.scene_key,
            title=title[:200],
            og_description=description[:300],
        )
        session.add(publication)
        await session.flush()
    else:
        publication = existing
        publication.page_id = page.id
        publication.scene_key = page.scene_key or publication.scene_key
        publication.title = title[:200]
        publication.og_description = description[:300]
        publication.updated_at = datetime.now(UTC)

    await _write_share_images(publication)
    await session.commit()
    return publication, republished


async def external_view_count(session: AsyncSession, conversation_id: uuid.UUID | None) -> int:
    if conversation_id is None:
        return 0
    result = await session.execute(
        select(func.count())
        .select_from(PageViewEvent)
        .where(PageViewEvent.conversation_id == conversation_id, PageViewEvent.is_owner_view.is_(False))
    )
    return int(result.scalar_one() or 0)


async def _page_display_title(session: AsyncSession, page: Page) -> str:
    """优先用生成页自己的 <title>。page.title 来自用户输入，常常是一整句需求。"""
    if page.current_version_id is not None:
        version = await session.get(PageVersion, page.current_version_id)
        if version is not None:
            try:
                html = await create_storage_provider().get_text(version.storage_key)
            except Exception:
                html = ""
            if html:
                soup = BeautifulSoup(html, "html.parser")
                text = soup.title.get_text(" ", strip=True) if soup.title else ""
                if text and text != "生成页面":
                    return text
    return page.title


async def _unique_slug(session: AsyncSession, slug: str) -> str:
    candidate = slug
    for _ in range(5):
        taken = await session.scalar(select(PagePublication.id).where(PagePublication.slug == candidate))
        if taken is None:
            return candidate
        candidate = f"p-{uuid.uuid4().hex[:10]}"
    raise ValueError("暂时无法分配发布链接，请重试")


async def _write_share_images(publication: PagePublication) -> None:
    url = publication_url(publication.slug)
    qr = render_qr_png(url)
    subtitle = publication.og_description or "由星页 StarPage 生成"
    og = render_card_png(title=publication.title, subtitle=subtitle, qr_png=qr, wide=True)
    poster = render_card_png(title=publication.title, subtitle=subtitle, qr_png=qr, wide=False)
    storage = create_storage_provider()
    og_key = f"share-cards/{publication.id}/og.png"
    poster_key = f"share-cards/{publication.id}/poster.png"
    await storage.put_bytes(og_key, og, "image/png")
    await storage.put_bytes(poster_key, poster, "image/png")
    publication.og_image_key = og_key
    publication.poster_image_key = poster_key
