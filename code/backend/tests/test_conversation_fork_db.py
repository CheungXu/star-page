"""会话分叉集成测试：真实建批次、看详情、删会话。需要一个可随意写入的空库（跑过迁移）。

    TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/stars_page_test \
        .venv/bin/python -m pytest tests/test_conversation_fork_db.py

未设置 TEST_DATABASE_URL 时跳过。不要指向线上库：测试会写入用户、会话和页面数据。
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
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="未设置 TEST_DATABASE_URL")

if TEST_DATABASE_URL:
    from sqlalchemy import select  # noqa: E402
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

    from app.api import routes_conversations  # noqa: E402
    from app.models.entities import Conversation, Page, User  # noqa: E402
    from app.services.billing.account import BillingService  # noqa: E402
    from app.services.conversation_tree import view_batches  # noqa: E402
    from app.services.generation_service import GenerationService  # noqa: E402


async def _noop(*_args, **_kwargs) -> None:
    return None


def test_fork_flow(monkeypatch):
    monkeypatch.setattr(BillingService, "ensure_can_start_batch", _noop)
    monkeypatch.setattr(BillingService, "record_anon_batch_started", _noop)
    monkeypatch.setattr(GenerationService, "_resolve_model_keys", lambda self, keys, allowed_keys=None: list(keys))
    asyncio.run(_run_with_engine(monkeypatch))


async def _run_with_engine(monkeypatch) -> None:
    engine = create_async_engine(TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1))
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(routes_conversations, "AsyncSessionLocal", session_factory)
    try:
        await _run_fork_flow(session_factory, monkeypatch)
    finally:
        await engine.dispose()


async def _run_fork_flow(session_factory, monkeypatch) -> None:
    async with session_factory() as session:
        user = User(username=f"fork-{uuid.uuid4().hex[:12]}", display_name="分叉测试")
        session.add(user)
        await session.commit()

    async def actor(_session, _request):
        return user

    monkeypatch.setattr(routes_conversations, "get_optional_actor", actor)

    async def create(prompt: str, models: list[str], conversation_id=None, base_page_id=None):
        async with session_factory() as session:
            creation = await GenerationService(session).create_batch(
                prompt=prompt,
                selected_model_keys=models,
                conversation_id=conversation_id,
                base_page_id=base_page_id,
                user=user,
            )
            for run in creation.runs:
                page = await session.get(Page, run.page_id)
                page.status = "ready"
            await session.commit()
            return creation

    async def page_of(creation, index: int = 0) -> Page:
        async with session_factory() as session:
            return await session.get(Page, creation.runs[index].page_id)

    async def view_ids(conversation_id) -> list[uuid.UUID]:
        async with session_factory() as session:
            conversation = await session.get(Conversation, conversation_id)
            return [batch.id for batch in await view_batches(session, conversation)]

    # 原会话 X：三轮线性续写。
    r1 = await create("第一轮", ["a"])
    x = r1.conversation_id
    r2 = await create("第二轮", ["a"], x, r1.runs[0].page_id)
    r3 = await create("第三轮", ["a"], x, r2.runs[0].page_id)
    assert r2.conversation_id == x and r3.conversation_id == x
    assert r2.forked_from is None and r3.forked_from is None
    assert await view_ids(x) == [r1.batch_id, r2.batch_id, r3.batch_id]

    # 选第 2 轮的结果、换模型 b：分叉出 X'，原会话不变。
    r4 = await create("从第二轮分叉", ["b"], x, r2.runs[0].page_id)
    x1 = r4.conversation_id
    assert x1 != x
    assert (r4.forked_from.conversation_id, r4.forked_from.round_number, r4.forked_from.title) == (x, 2, "第一轮")
    assert (await page_of(r4)).parent_page_id == r2.runs[0].page_id
    assert await view_ids(x1) == [r1.batch_id, r2.batch_id, r4.batch_id]
    assert await view_ids(x) == [r1.batch_id, r2.batch_id, r3.batch_id]
    async with session_factory() as session:
        fork = await session.get(Conversation, x1)
        assert fork.origin == "fork"
        assert (fork.forked_from_conversation_id, fork.forked_from_batch_id) == (x, r2.batch_id)

    # 在 X' 里选继承来的第 1 轮：指针指向真正拥有该轮的 X。
    r5 = await create("从第一轮分叉", ["a"], x1, r1.runs[0].page_id)
    x2 = r5.conversation_id
    assert x2 not in {x, x1}
    assert (r5.forked_from.conversation_id, r5.forked_from.round_number) == (x, 1)
    assert await view_ids(x2) == [r1.batch_id, r5.batch_id]

    # 别的分支上的节点不在 X'' 的视图里，不能当基点。
    with pytest.raises(ValueError):
        await create("越界", ["a"], x2, r3.runs[0].page_id)

    # X' 最后一轮继续：留在 X'。
    r6 = await create("X' 第四轮", ["b"], x1, r4.runs[0].page_id)
    assert r6.conversation_id == x1 and r6.forked_from is None

    # X' 并行续写加入新模型 c：b 接自己的最新节点，c 接最近一轮的结果，都不会串到 X 的第 3 轮。
    r7 = await create("并行", ["b", "c"], x1)
    assert (await page_of(r7, 0)).parent_page_id == r6.runs[0].page_id
    assert (await page_of(r7, 1)).parent_page_id == r6.runs[0].page_id

    # 每轮自由换模型：只留 c，c 接自己那一版；下一轮只选 b，b 上一轮没参与，接上一轮 c 的结果而不是自己更早那版。
    r8 = await create("只用 c", ["c"], x1)
    assert (await page_of(r8)).parent_page_id == r7.runs[1].page_id
    r9 = await create("换回 b", ["b"], x1)
    assert (await page_of(r9)).parent_page_id == r8.runs[0].page_id

    # 详情：继承轮次在前并带标记，分叉来源是 X 第 2 轮。
    detail = await routes_conversations.get_conversation(x1, None)
    assert [batch.batch_id for batch in detail.batches] == [
        r1.batch_id, r2.batch_id, r4.batch_id, r6.batch_id, r7.batch_id, r8.batch_id, r9.batch_id,
    ]
    assert [batch.inherited for batch in detail.batches] == [True, True, False, False, False, False, False]
    assert detail.forked_from is not None
    assert (detail.forked_from.conversation_id, detail.forked_from.round_number) == (x, 2)
    assert detail.forked_from.available is True
    inherited_node = detail.batches[0].nodes[0]
    assert f"/p/{x}/{inherited_node.page_id}" in inherited_node.page_url

    listing = {
        item.id: item
        for item in await routes_conversations.list_conversations(None, favorite_only=False, q=None, limit=50)
    }
    assert listing[x].forked_from is None
    assert listing[x1].forked_from.round_number == 2
    assert listing[x2].forked_from.round_number == 1

    async def deleted(creation) -> bool:
        return (await page_of(creation)).deleted_at is not None

    # 删 X：第 3 轮没人继承，删掉；第 1、2 轮仍被 X'、X'' 继承，保留。
    await routes_conversations.delete_conversation(x, None)
    assert await deleted(r3)
    assert not await deleted(r1) and not await deleted(r2)
    detail = await routes_conversations.get_conversation(x1, None)
    assert len(detail.batches) == 7 and detail.forked_from.available is False

    # 删 X'：它自己的节点删掉；X 第 2 轮只剩它继承，跟着删；第 1 轮还被 X'' 继承。
    await routes_conversations.delete_conversation(x1, None)
    assert await deleted(r4) and await deleted(r2)
    assert not await deleted(r1)

    # 删 X''：最后一个继承者没了，第 1 轮也删掉。
    await routes_conversations.delete_conversation(x2, None)
    assert await deleted(r5) and await deleted(r1)

    async with session_factory() as session:
        remaining = await session.execute(
            select(Page).where(Page.owner_user_id == user.id, Page.deleted_at.is_(None))
        )
        assert remaining.scalars().all() == []
