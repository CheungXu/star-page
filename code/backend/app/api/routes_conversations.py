from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, HTTPException
from sqlalchemy import desc, func, select

from app.core.config import get_model_registry, get_settings
from app.core.database import AsyncSessionLocal
from app.core.default_user import ensure_default_user
from app.models.entities import Conversation, GenerationBatch, GenerationTask, Page
from app.schemas.conversations import (
    ConversationBatch as ConversationBatchSchema,
)
from app.schemas.conversations import (
    ConversationDetail,
    ConversationListItem,
    ConversationNode,
)
from app.schemas.generation import ModelInfo

router = APIRouter(tags=["conversations"])


@router.get("/api/models", response_model=list[ModelInfo])
async def list_models() -> list[ModelInfo]:
    registry = get_model_registry()
    defaults = set(registry.default_model_keys)
    return [
        ModelInfo(
            key=model.key,
            label=model.label,
            provider=model.provider,
            is_default=model.key in defaults,
            available=model.available,
        )
        for model in registry.models.values()
    ]


@router.get("/api/conversations", response_model=list[ConversationListItem])
async def list_conversations() -> list[ConversationListItem]:
    async with AsyncSessionLocal() as session:
        user = await ensure_default_user(session)
        result = await session.execute(
            select(Conversation)
            .where(Conversation.owner_user_id == user.id, Conversation.deleted_at.is_(None))
            .order_by(desc(Conversation.updated_at), desc(Conversation.created_at))
            .limit(50)
        )
        conversations = result.scalars().all()

        items: list[ConversationListItem] = []
        for conversation in conversations:
            latest_result = await session.execute(
                select(GenerationBatch)
                .where(GenerationBatch.conversation_id == conversation.id)
                .order_by(desc(GenerationBatch.created_at))
                .limit(1)
            )
            latest = latest_result.scalar_one_or_none()

            node_count_result = await session.execute(
                select(func.count(Page.id)).where(
                    Page.conversation_id == conversation.id, Page.deleted_at.is_(None)
                )
            )
            node_count = int(node_count_result.scalar_one())

            items.append(
                ConversationListItem(
                    id=conversation.id,
                    title=conversation.title,
                    origin=conversation.origin,
                    model_keys=list(latest.selected_models) if latest else [],
                    node_count=node_count,
                    latest_batch_status=latest.status if latest else None,
                    created_at=conversation.created_at,
                    updated_at=conversation.updated_at,
                )
            )
        return items


@router.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: uuid.UUID) -> ConversationDetail:
    settings = get_settings()
    registry = get_model_registry()

    async with AsyncSessionLocal() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="会话不存在")

        batches_result = await session.execute(
            select(GenerationBatch)
            .where(GenerationBatch.conversation_id == conversation.id)
            .order_by(GenerationBatch.created_at.asc())
        )
        batches = batches_result.scalars().all()

        pages_result = await session.execute(
            select(Page).where(Page.conversation_id == conversation.id, Page.deleted_at.is_(None))
        )
        pages = pages_result.scalars().all()

        pages_by_batch: dict[uuid.UUID, list[Page]] = defaultdict(list)
        for page in pages:
            if page.batch_id is not None:
                pages_by_batch[page.batch_id].append(page)

        tasks_by_page: dict[uuid.UUID, GenerationTask] = {}
        page_ids = [page.id for page in pages]
        if page_ids:
            tasks_result = await session.execute(
                select(GenerationTask).where(GenerationTask.page_id.in_(page_ids))
            )
            for task in tasks_result.scalars().all():
                tasks_by_page[task.page_id] = task

        batch_items: list[ConversationBatchSchema] = []
        for batch in batches:
            selected = list(batch.selected_models or [])
            batch_pages = pages_by_batch.get(batch.id, [])
            batch_pages.sort(key=lambda page: _model_order(page.model_key, selected))

            nodes: list[ConversationNode] = []
            for page in batch_pages:
                task = tasks_by_page.get(page.id)
                model = registry.get(page.model_key) if page.model_key else None
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
                        page_url=f"{settings.public_base_url.rstrip('/')}/p/{page.id}",
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
                )
            )

        return ConversationDetail(
            id=conversation.id,
            title=conversation.title,
            origin=conversation.origin,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            batches=batch_items,
        )


def _model_order(model_key: str | None, selected: list[str]) -> int:
    if model_key and model_key in selected:
        return selected.index(model_key)
    return len(selected)
