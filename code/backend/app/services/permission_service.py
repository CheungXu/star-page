from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Page, PagePermission, User


async def can_view_page(session: AsyncSession, page: Page, user: User | None) -> bool:
    if page.deleted_at is not None or page.status == "deleted":
        return False

    if page.visibility == "public":
        return True

    if user is None:
        return False

    if page.owner_user_id == user.id:
        return True

    if page.visibility == "restricted":
        result = await session.execute(
            select(PagePermission).where(
                PagePermission.page_id == page.id,
                PagePermission.user_id == user.id,
                PagePermission.role.in_(["owner", "viewer", "editor"]),
            )
        )
        return result.scalar_one_or_none() is not None

    return False
