"""固定发布链接：一个会话一个 slug，重新发布只切换当前页面。"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from bs4 import BeautifulSoup

from app.models.entities import Page, PagePublication, PageVersion, PageViewEvent
from app.services.scenes.catalog import get_scene
from app.services.share_card import CARD_ALIGNS, render_card_png, render_qr_png
from app.services.storage.factory import create_storage_provider

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,47}$")

DEFAULT_SUBTITLE = "由星页 StarPage 生成"
CUSTOM_TITLE_MAX = 60
CUSTOM_SUBTITLE_MAX = 120


@dataclass
class PublishResult:
    publication: PagePublication
    republished: bool
    default_title: str


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
) -> PublishResult:
    """发布或重新发布。重新发布只切换页面，用户改写过的分享卡片标题 / 副标题 / 对齐保留。"""
    if page.conversation_id is None:
        raise ValueError("页面还没有会话，无法发布固定链接")
    if page.status != "ready":
        raise ValueError("页面尚未生成完成")
    if page.moderation_status == "blocked":
        raise ValueError("该页面因违规已下架，无法发布")

    existing = await session.scalar(select(PagePublication).where(PagePublication.conversation_id == page.conversation_id))
    default_title = await _page_display_title(session, page)
    republished = existing is not None

    if existing is None:
        slug = await _unique_slug(session, allocate_slug(preferred_slug))
        publication = PagePublication(
            owner_user_id=page.owner_user_id,
            conversation_id=page.conversation_id,
            page_id=page.id,
            slug=slug,
            scene_key=page.scene_key,
            title=default_title[:200],
            card_align="center",
        )
        session.add(publication)
        await session.flush()
    else:
        publication = existing
        publication.page_id = page.id
        publication.scene_key = page.scene_key or publication.scene_key
        publication.updated_at = datetime.now(UTC)

    apply_card_texts(publication, default_title=default_title)
    await _write_share_images(publication)
    await session.commit()
    return PublishResult(publication=publication, republished=republished, default_title=default_title)


async def update_share_card(session: AsyncSession, publication: PagePublication, changes: dict[str, str | None]) -> str:
    """改写分享卡片：changes 里出现的键才修改，值为 None 或空白表示恢复默认。重画分享图，返回默认标题。"""
    if "title" in changes:
        publication.custom_title = clean_card_text(changes["title"], CUSTOM_TITLE_MAX)
    if "subtitle" in changes:
        publication.custom_subtitle = clean_card_text(changes["subtitle"], CUSTOM_SUBTITLE_MAX)
    if "align" in changes:
        align = changes["align"]
        publication.card_align = align if align in CARD_ALIGNS else "center"
    page = await session.get(Page, publication.page_id)
    default_title = await _page_display_title(session, page) if page is not None else publication.title
    apply_card_texts(publication, default_title=default_title)
    publication.updated_at = datetime.now(UTC)
    await _write_share_images(publication)
    await session.commit()
    return default_title


def clean_card_text(value: str | None, limit: int) -> str | None:
    """去掉首尾空白、把连续空白压成一个；空串视为未填写。"""
    clean = " ".join((value or "").split())
    return clean[:limit] or None


def default_subtitle(scene_key: str | None) -> str:
    scene = get_scene(scene_key)
    return scene.tagline if scene and scene.tagline else DEFAULT_SUBTITLE


def apply_card_texts(publication: PagePublication, *, default_title: str) -> None:
    """把生效的标题 / 副标题写回 title、og_description：用户改写过的优先，否则用默认。"""
    publication.title = (publication.custom_title or default_title or "星页")[:200]
    publication.og_description = (publication.custom_subtitle or default_subtitle(publication.scene_key))[:300]


def is_card_customized(publication: PagePublication) -> bool:
    return bool(publication.custom_title or publication.custom_subtitle or (publication.card_align or "center") != "center")


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
                text = await asyncio.to_thread(_html_title, html)
                if text and text != "生成页面":
                    return text
    return page.title


def _html_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.title.get_text(" ", strip=True) if soup.title else ""


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
    subtitle = publication.og_description or DEFAULT_SUBTITLE
    og, poster = await asyncio.to_thread(
        _render_share_images, url, publication.title, subtitle, publication.card_align or "center"
    )
    storage = create_storage_provider()
    og_key = f"share-cards/{publication.id}/og.png"
    poster_key = f"share-cards/{publication.id}/poster.png"
    await storage.put_bytes(og_key, og, "image/png")
    await storage.put_bytes(poster_key, poster, "image/png")
    publication.og_image_key = og_key
    publication.poster_image_key = poster_key


def _render_share_images(url: str, title: str, subtitle: str, align: str) -> tuple[bytes, bytes]:
    qr = render_qr_png(url)
    og = render_card_png(title=title, subtitle=subtitle, qr_png=qr, wide=True)
    poster = render_card_png(title=title, subtitle=subtitle, qr_png=qr, wide=False, align=align)
    return og, poster
