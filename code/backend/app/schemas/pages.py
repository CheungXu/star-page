from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PageResponse(BaseModel):
    id: UUID
    title: str
    visibility: str
    status: str
    current_version_id: UUID | None
    url: str
    created_at: datetime
    updated_at: datetime


class PageHistoryItem(BaseModel):
    id: UUID
    task_id: UUID | None
    title: str
    prompt: str
    file_names: list[str]
    page_url: str
    page_status: str
    generation_status: str | None
    created_at: datetime
    updated_at: datetime
