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
