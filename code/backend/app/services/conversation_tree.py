"""会话分叉：一条会话能看到哪些轮次、何时分叉、删除时哪些节点仍要保留。

一条会话 = 生成树上的一条线。origin='fork' 的会话通过
(forked_from_conversation_id, forked_from_batch_id) 接在来源会话的某一轮之后：
那一轮及之前从来源会话继承，之后的轮次归自己。分叉指针总是指向真正拥有那一轮的会话，
所以沿指针往上走一遍就能拼出完整历史。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Conversation, GenerationBatch, Page

_MAX_CHAIN_DEPTH = 100


@dataclass(frozen=True)
class ForkSource:
    """分叉会话的来源：从哪条会话的第几轮（从 1 开始）分出来；来源被删后 available 为假。"""

    conversation_id: uuid.UUID
    title: str
    round_number: int
    available: bool


@dataclass(frozen=True)
class ForkLink:
    """分叉会话指向来源会话的那条边：继承 parent_id 会话直到 until_batch_id（含）的轮次。"""

    parent_id: uuid.UUID
    until_batch_id: uuid.UUID


def take_until(batches: list[GenerationBatch], until_batch_id: uuid.UUID | None) -> list[GenerationBatch]:
    """按先后排好的批次截到 until_batch_id（含）；不传则全取，找不到则一轮都不取。"""
    if until_batch_id is None:
        return list(batches)
    for index, batch in enumerate(batches):
        if batch.id == until_batch_id:
            return list(batches[: index + 1])
    return []


def inherited_until(
    start_id: uuid.UUID, target_id: uuid.UUID, links: dict[uuid.UUID, ForkLink]
) -> uuid.UUID | None:
    """从 start_id 沿分叉链往上走，返回它最终继承了 target_id 会话的哪一轮；不经过 target 时为空。"""
    current = start_id
    visited: set[uuid.UUID] = set()
    while current in links and current not in visited and len(visited) < _MAX_CHAIN_DEPTH:
        visited.add(current)
        link = links[current]
        if link.parent_id == target_id:
            return link.until_batch_id
        current = link.parent_id
    return None


def pick_fallback_parent(pages: list[Page], selected_models: list[str]) -> uuid.UUID | None:
    """没有自己那一版可接的模型接到哪个结果上：按该轮的模型顺序取第一个已完成的节点，都没完成就取第一个。"""
    if not pages:
        return None

    def order(page: Page) -> int:
        return selected_models.index(page.model_key) if page.model_key in selected_models else len(selected_models)

    ordered = sorted(pages, key=order)
    ready = [page for page in ordered if page.status == "ready"]
    return (ready or ordered)[0].id


def protected_batch_ids(own_batch_ids: list[uuid.UUID], until_batch_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """各分支继承的都是同一会话的前缀，取最长的那段。"""
    positions = [own_batch_ids.index(batch_id) for batch_id in until_batch_ids if batch_id in own_batch_ids]
    if not positions:
        return set()
    return set(own_batch_ids[: max(positions) + 1])


async def own_batches(session: AsyncSession, conversation_id: uuid.UUID) -> list[GenerationBatch]:
    result = await session.execute(
        select(GenerationBatch)
        .where(GenerationBatch.conversation_id == conversation_id)
        .order_by(GenerationBatch.created_at.asc())
    )
    return list(result.scalars().all())


async def view_batches(session: AsyncSession, conversation: Conversation) -> list[GenerationBatch]:
    """会话里能看到的全部轮次，按先后：继承来的在前，自己的在后。来源会话被删也照样继承。"""
    segments: list[tuple[uuid.UUID, uuid.UUID | None]] = []
    current: Conversation | None = conversation
    until: uuid.UUID | None = None
    visited: set[uuid.UUID] = set()
    while current is not None and current.id not in visited and len(visited) < _MAX_CHAIN_DEPTH:
        visited.add(current.id)
        segments.append((current.id, until))
        if current.forked_from_conversation_id is None or current.forked_from_batch_id is None:
            break
        until = current.forked_from_batch_id
        current = await session.get(Conversation, current.forked_from_conversation_id)

    batches: list[GenerationBatch] = []
    for conversation_id, until_batch_id in reversed(segments):
        batches.extend(take_until(await own_batches(session, conversation_id), until_batch_id))
    return batches


async def fork_source(
    session: AsyncSession, conversation: Conversation, view: list[GenerationBatch]
) -> ForkSource | None:
    """view 为该会话的 view_batches；继承了几轮，分叉点就是来源会话的第几轮。"""
    if conversation.forked_from_conversation_id is None:
        return None
    source = await session.get(Conversation, conversation.forked_from_conversation_id)
    if source is None:
        return None
    return ForkSource(
        conversation_id=source.id,
        title=source.title,
        round_number=sum(1 for batch in view if batch.conversation_id != conversation.id),
        available=source.deleted_at is None,
    )


async def release_deleted_pages(session: AsyncSession, conversation: Conversation, now: datetime) -> None:
    """会话软删后调用：软删它的节点，但保留仍被未删除分支继承的那段前缀。

    沿分叉链往上、同样已删的来源会话也重新结算一次：最后一个继承它的分支被删后，那段前缀也一起删掉。
    """
    result = await session.execute(
        select(Conversation).where(
            Conversation.owner_user_id == conversation.owner_user_id,
            Conversation.forked_from_conversation_id.is_not(None),
        )
    )
    forks = list(result.scalars().all())
    links = {
        fork.id: ForkLink(parent_id=fork.forked_from_conversation_id, until_batch_id=fork.forked_from_batch_id)
        for fork in forks
        if fork.forked_from_conversation_id is not None and fork.forked_from_batch_id is not None
    }
    live_fork_ids = [fork.id for fork in forks if fork.deleted_at is None]

    candidates = [conversation.id]
    current = conversation.id
    while current in links and len(candidates) < _MAX_CHAIN_DEPTH:
        parent = await session.get(Conversation, links[current].parent_id)
        if parent is None or parent.deleted_at is None:
            break
        candidates.append(parent.id)
        current = parent.id

    for candidate_id in candidates:
        untils = [
            until
            for until in (inherited_until(fork_id, candidate_id, links) for fork_id in live_fork_ids)
            if until is not None
        ]
        protected = protected_batch_ids([batch.id for batch in await own_batches(session, candidate_id)], untils)
        statement = (
            update(Page)
            .where(Page.conversation_id == candidate_id, Page.deleted_at.is_(None))
            .values(deleted_at=now)
        )
        if protected:
            statement = statement.where(or_(Page.batch_id.is_(None), Page.batch_id.not_in(protected)))
        await session.execute(statement)
