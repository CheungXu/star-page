from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class GenerationCreateRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)


class GenerationCreateResponse(BaseModel):
    task_id: UUID
    page_id: UUID
    status: str
    page_url: str


class GenerationEventPayload(BaseModel):
    type: str
    text: str | None = None
    page_id: UUID | None = None
    url: str | None = None
    message: str | None = None
