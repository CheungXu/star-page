from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class GenerationCreateRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    models: list[str] = Field(default_factory=list)
    conversation_id: UUID | None = None
    base_page_id: UUID | None = None


class GenerationRunItem(BaseModel):
    task_id: UUID
    page_id: UUID
    model_key: str
    model_label: str
    page_url: str
    status: str = "pending"


class GenerationCreateResponse(BaseModel):
    conversation_id: UUID
    batch_id: UUID
    kind: str
    runs: list[GenerationRunItem]


class ModelInfo(BaseModel):
    key: str
    label: str
    provider: str
    is_default: bool
    available: bool


class GenerationEventPayload(BaseModel):
    type: str
    text: str | None = None
    page_id: UUID | None = None
    url: str | None = None
    message: str | None = None
    model_key: str | None = None
