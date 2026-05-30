from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ConversationListItem(BaseModel):
    id: UUID
    title: str
    origin: str
    model_keys: list[str]
    node_count: int
    latest_batch_status: str | None
    created_at: datetime
    updated_at: datetime


class ConversationNode(BaseModel):
    page_id: UUID
    task_id: UUID | None
    model_key: str | None
    model_label: str | None
    model_name: str | None
    parent_page_id: UUID | None
    page_status: str
    generation_status: str | None
    page_url: str


class ConversationBatch(BaseModel):
    batch_id: UUID
    kind: str
    base_page_id: UUID | None
    selected_models: list[str]
    status: str
    prompt: str
    user_prompt: str | None
    file_names: list[str]
    created_at: datetime
    nodes: list[ConversationNode]


class ConversationDetail(BaseModel):
    id: UUID
    title: str
    origin: str
    created_at: datetime
    updated_at: datetime
    batches: list[ConversationBatch]
