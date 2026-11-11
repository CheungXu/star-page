from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_billing_config, get_model_registry, get_settings
from app.core.auth import get_optional_actor
from app.core.database import AsyncSessionLocal
from app.core.urls import build_page_url
from app.models.entities import Conversation, CreditTransaction, GenerationBatch, GenerationTask, Page, PageVersion
from app.schemas.conversations import (
    ConversationBatch as ConversationBatchSchema,
)
from app.schemas.conversations import (
    ConversationBilling,
    ConversationDetail,
    ConversationForkSource,
    ConversationListItem,
    ConversationNode,
    ConversationUsage,
    ConversationUpdate,
)
from app.schemas.generation import ModelInfo
from app.services.conversation_tree import fork_source, release_deleted_pages, view_batches
from app.services.document_parser.pipeline import discard_snapshots

router = APIRouter(tags=["conversations"])


@router.get("/api/models", response_model=list[ModelInfo])
async def list_models() -> list[ModelInfo]:
    registry = get_model_registry()
    billing = get_billing_config()
    defaults = set(registry.default_model_keys)
    anon_allowed = set(billing.anon_allowed_models)
    anon_defaults = set(billing.anon_default_models)
    return [
        ModelInfo(
            key=model.key,
            label=model.label,
            provider=model.provider,
            is_default=model.key in defaults,
            available=model.available,
            anon_allowed=model.key in anon_allowed,
            anon_default=model.key in anon_defaults,
        )
        for model in registry.models.values()
    ]


@router.get("/api/conversations", response_model=list[ConversationListItem])
async def list_conversations(
    request: Request,
    favorite_only: bool = False,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[ConversationListItem]:
    async with AsyncSessionLocal() as session:
        user = await get_optional_actor(session, request)
        if user is None:
            return []
        conditions = [Conversation.owner_user_id == user.id, Conversation.deleted_at.is_(None)]
        if favorite_only:
            conditions.append(Conversation.is_favorite.is_(True))

        keyword = q.strip() if q else ""
        if keyword:
            pattern = f"%{keyword}%"
            prompt_match_ids = select(GenerationBatch.conversation_id).where(
                or_(
                    GenerationBatch.user_prompt.ilike(pattern),
                    GenerationBatch.prompt.ilike(pattern),
                )
            )
            conditions.append(or_(Conversation.title.ilike(pattern), Conversation.id.in_(prompt_match_ids)))

        result = await session.execute(
            select(Conversation)
            .where(*conditions)
            .order_by(desc(Conversation.updated_at), desc(Conversation.created_at))
            .limit(limit)
        )
        conversations = result.scalars().all()

        return [await _build_conversation_list_item(session, conversation) for conversation in conversations]


@router.patch("/api/conversations/{conversation_id}", response_model=ConversationListItem)
async def update_conversation(
    conversation_id: uuid.UUID, payload: ConversationUpdate, request: Request
) -> ConversationListItem:
    async with AsyncSessionLocal() as session:
        user = await get_optional_actor(session, request)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None or conversation.owner_user_id != user.id or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="会话不存在")

        conversation.is_favorite = payload.is_favorite
        conversation.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(conversation)
        return await _build_conversation_list_item(session, conversation)


@router.delete("/api/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: uuid.UUID, request: Request) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_optional_actor(session, request)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None or conversation.owner_user_id != user.id or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="会话不存在")

        now = datetime.now(UTC)
        conversation.deleted_at = now
        conversation.updated_at = now
        # 级联软删会话下的节点，使其分享链接 /p/{conversation_id}/{page_id} 同步失效；
        # 仍被未删除分支继承的那段历史保留。
        await release_deleted_pages(session, conversation, now)
        snapshots = await _release_private_snapshots(session, conversation.id)
        await session.commit()
    await discard_snapshots(snapshots)


async def _release_private_snapshots(session: AsyncSession, conversation_id: uuid.UUID) -> list[dict]:
    """节点全部删掉的批次，私有整页快照也随之删除。仍被分叉会话继承的批次节点还在，不会被选中。"""
    live = select(Page.id).where(Page.batch_id == GenerationBatch.id, Page.deleted_at.is_(None))
    result = await session.execute(
        select(GenerationBatch).where(GenerationBatch.conversation_id == conversation_id, ~live.exists())
    )
    released: list[dict] = []
    for batch in result.scalars():
        if batch.visual_snapshots:
            released.extend(batch.visual_snapshots)
            batch.visual_snapshots = []
    return released


@router.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: uuid.UUID, request: Request) -> ConversationDetail:
    settings = get_settings()
    registry = get_model_registry()

    async with AsyncSessionLocal() as session:
        user = await get_optional_actor(session, request)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None or conversation.owner_user_id != user.id or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="会话不存在")

        batches = await view_batches(session, conversation)
        batch_ids = [batch.id for batch in batches]

        pages: list[Page] = []
        if batch_ids:
            pages_result = await session.execute(
                select(Page).where(Page.batch_id.in_(batch_ids), Page.deleted_at.is_(None))
            )
            pages = list(pages_result.scalars().all())

        pages_by_batch: dict[uuid.UUID, list[Page]] = defaultdict(list)
        for page in pages:
            if page.batch_id is not None:
                pages_by_batch[page.batch_id].append(page)

        versions_by_page: dict[uuid.UUID, PageVersion] = {}
        current_version_ids = [page.current_version_id for page in pages if page.current_version_id is not None]
        if current_version_ids:
            versions_result = await session.execute(
                select(PageVersion).where(PageVersion.id.in_(current_version_ids))
            )
            for version in versions_result.scalars().all():
                versions_by_page[version.page_id] = version

        tasks_by_page: dict[uuid.UUID, GenerationTask] = {}
        page_ids = [page.id for page in pages]
        if page_ids:
            tasks_result = await session.execute(
                select(GenerationTask).where(GenerationTask.page_id.in_(page_ids))
            )
            for task in tasks_result.scalars().all():
                tasks_by_page[task.page_id] = task

        billing_by_version: dict[str, CreditTransaction] = {}
        if current_version_ids:
            txn_result = await session.execute(
                select(CreditTransaction).where(
                    CreditTransaction.ref_type == "page_version",
                    CreditTransaction.ref_id.in_([str(version_id) for version_id in current_version_ids]),
                    CreditTransaction.type == "consume",
                )
            )
            for txn in txn_result.scalars().all():
                if txn.ref_id:
                    billing_by_version[txn.ref_id] = txn

        batch_items: list[ConversationBatchSchema] = []
        for batch in batches:
            selected = list(batch.selected_models or [])
            batch_pages = pages_by_batch.get(batch.id, [])
            batch_pages.sort(key=lambda page: _model_order(page.model_key, selected))

            nodes: list[ConversationNode] = []
            for page in batch_pages:
                task = tasks_by_page.get(page.id)
                model = registry.get(page.model_key) if page.model_key else None
                version = versions_by_page.get(page.id)
                nodes.append(
                    ConversationNode(
                        page_id=page.id,
                        task_id=task.id if task else None,
                        model_key=page.model_key,
                        model_label=model.label if model else page.model_key,
                        model_name=page.model_name,
                        parent_page_id=page.parent_page_id,
                        page_status=page.status,
                        generation_status=task.status if task else None,
                        page_url=build_page_url(settings, page.conversation_id, page.id),
                        usage=_build_usage(version),
                        billing=_build_billing(billing_by_version.get(str(version.id)) if version else None),
                        duration_ms=_task_duration_ms(task),
                    )
                )

            batch_items.append(
                ConversationBatchSchema(
                    batch_id=batch.id,
                    kind=batch.kind,
                    base_page_id=batch.base_page_id,
                    selected_models=selected,
                    status=batch.status,
                    prompt=batch.prompt,
                    user_prompt=batch.user_prompt,
                    file_names=list(batch.input_file_names or []),
                    created_at=batch.created_at,
                    nodes=nodes,
                    inherited=batch.conversation_id != conversation.id,
                )
            )

        return ConversationDetail(
            id=conversation.id,
            title=conversation.title,
            origin=conversation.origin,
            scene_key=conversation.scene_key,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            batches=batch_items,
            forked_from=await _fork_source(session, conversation, batches),
        )


def _model_order(model_key: str | None, selected: list[str]) -> int:
    if model_key and model_key in selected:
        return selected.index(model_key)
    return len(selected)


def _build_usage(version: PageVersion | None) -> ConversationUsage | None:
    if version is None:
        return None
    if version.input_tokens is None and version.output_tokens is None:
        return None
    return ConversationUsage(
        input_tokens=version.input_tokens,
        output_tokens=version.output_tokens,
        total_tokens=version.total_tokens,
        cached_input_tokens=version.cached_input_tokens,
        reasoning_tokens=version.reasoning_tokens,
    )


def _build_billing(txn: CreditTransaction | None) -> ConversationBilling | None:
    """只返回扣掉的积分和优惠。实际金额留在流水和版本上，由管理后台查看。"""
    if txn is None:
        return None
    charged = max(-int(txn.credits_delta), 0)
    discount = max(int(txn.training_discount_credits or 0), 0)
    if charged == 0 and discount == 0:
        return None
    return ConversationBilling(credits_charged=charged, discount_credits=discount)


def _task_duration_ms(task: GenerationTask | None) -> int | None:
    if task is None or task.started_at is None or task.finished_at is None:
        return None
    return max(int((task.finished_at - task.started_at).total_seconds() * 1000), 0)


async def _build_conversation_list_item(
    session: AsyncSession,
    conversation: Conversation,
) -> ConversationListItem:
    latest_result = await session.execute(
        select(GenerationBatch)
        .where(GenerationBatch.conversation_id == conversation.id)
        .order_by(desc(GenerationBatch.created_at))
        .limit(1)
    )
    latest = latest_result.scalar_one_or_none()

    node_count_result = await session.execute(
        select(func.count(Page.id)).where(Page.conversation_id == conversation.id, Page.deleted_at.is_(None))
    )
    node_count = int(node_count_result.scalar_one())

    forked_from = None
    if conversation.forked_from_conversation_id is not None:
        forked_from = await _fork_source(session, conversation, await view_batches(session, conversation))

    return ConversationListItem(
        id=conversation.id,
        title=conversation.title,
        origin=conversation.origin,
        is_favorite=conversation.is_favorite,
        model_keys=list(latest.selected_models) if latest else [],
        node_count=node_count,
        latest_batch_status=latest.status if latest else None,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        forked_from=forked_from,
    )


async def _fork_source(
    session: AsyncSession, conversation: Conversation, view: list[GenerationBatch]
) -> ConversationForkSource | None:
    source = await fork_source(session, conversation, view)
    return ConversationForkSource(**asdict(source)) if source else None
