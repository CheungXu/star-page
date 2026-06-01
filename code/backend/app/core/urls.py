from __future__ import annotations

import uuid

from app.core.config import Settings


def build_page_url(
    settings: Settings,
    conversation_id: uuid.UUID | None,
    page_id: uuid.UUID,
) -> str:
    """构造页面访问链接：体现"会话 -> 节点"归属的层级路径 /p/{conversation_id}/{page_id}。"""
    base = settings.public_base_url.rstrip("/")
    return f"{base}/p/{conversation_id}/{page_id}"
