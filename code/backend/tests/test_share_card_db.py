"""分享卡片集成测试：发布 → 改写标题 / 副标题 / 对齐 → 换版本重新发布 → 恢复默认。需要一个跑过迁移的空库。

    TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/stars_page_test \
    OBJECT_STORAGE_PROVIDER=local LOCAL_STORAGE_DIR=/tmp/sp-share-test \
        .venv/bin/python -m pytest tests/test_share_card_db.py

未设置 TEST_DATABASE_URL 时跳过。不要指向线上库：测试会写入用户、会话和页面数据；分享图写到本地存储目录。
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or os.environ.get("OBJECT_STORAGE_PROVIDER") != "local",
    reason="需要 TEST_DATABASE_URL 与 OBJECT_STORAGE_PROVIDER=local",
)

if TEST_DATABASE_URL:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

    from app.models.entities import Conversation, Page, PageVersion, User  # noqa: E402
    from app.services.publications import DEFAULT_SUBTITLE, publish_page, update_share_card  # noqa: E402
    from app.services.storage.factory import create_storage_provider  # noqa: E402


def test_share_card_flow():
    asyncio.run(_run())


async def _run() -> None:
    engine = create_async_engine(TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        await _flow(factory)
    finally:
        await engine.dispose()


async def _make_page(session, user: User, conversation: Conversation, html_title: str) -> Page:
    page = Page(owner_user_id=user.id, conversation_id=conversation.id, title="一整句用户需求", status="ready")
    session.add(page)
    await session.flush()
    key = f"pages/{page.id}/index.html"
    await create_storage_provider().put_text(key, f"<html><head><title>{html_title}</title></head><body>hi</body></html>")
    version = PageVersion(page_id=page.id, version_number=1, prompt="p", storage_key=key)
    session.add(version)
    await session.flush()
    page.current_version_id = version.id
    await session.commit()
    return page


async def _flow(factory) -> None:
    storage = create_storage_provider()
    async with factory() as session:
        user = User(username=f"share-{uuid.uuid4().hex[:12]}", display_name="分享测试")
        session.add(user)
        await session.flush()
        conversation = Conversation(owner_user_id=user.id, title="分享测试会话")
        session.add(conversation)
        await session.commit()
        first = await _make_page(session, user, conversation, "星尘回响 · 实时粒子观测站")
        second = await _make_page(session, user, conversation, "曜石之光")

        # 首次发布：标题取生成页 <title>，副标题默认，居中，未自定义。
        result = await publish_page(session, page=first, preferred_slug=None)
        publication = result.publication
        assert not result.republished
        assert (publication.title, publication.og_description, publication.card_align) == ("星尘回响 · 实时粒子观测站", DEFAULT_SUBTITLE, "center")
        poster_before = await storage.get_bytes(publication.poster_image_key)

        # 改写三项：生效值写回 title / og_description，海报重画。
        default_title = await update_share_card(
            session, publication, {"title": "  两万颗粒子的  引力之舞 ", "subtitle": "鼠标拖动，看它们如何回应你", "align": "left"}
        )
        assert default_title == "星尘回响 · 实时粒子观测站"
        assert (publication.title, publication.og_description, publication.card_align) == ("两万颗粒子的 引力之舞", "鼠标拖动，看它们如何回应你", "left")
        assert await storage.get_bytes(publication.poster_image_key) != poster_before

        # 换另一个版本重新发布：链接不变，用户改写的内容保留。
        again = await publish_page(session, page=second, preferred_slug=None)
        assert again.republished and again.publication.slug == publication.slug
        assert again.default_title == "曜石之光"
        assert again.publication.title == "两万颗粒子的 引力之舞"
        assert again.publication.card_align == "left"

        # 只改对齐不动标题；再把标题、副标题置空 → 恢复为当前版本的默认值。
        await update_share_card(session, again.publication, {"align": "right"})
        assert again.publication.title == "两万颗粒子的 引力之舞" and again.publication.card_align == "right"
        await update_share_card(session, again.publication, {"title": None, "subtitle": "", "align": None})
        assert (again.publication.title, again.publication.og_description, again.publication.card_align) == ("曜石之光", DEFAULT_SUBTITLE, "center")
        assert again.publication.custom_title is None and again.publication.custom_subtitle is None
