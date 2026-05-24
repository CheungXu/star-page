from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.entities import User


async def ensure_default_user(session: AsyncSession) -> User:
    settings = get_settings()
    result = await session.execute(select(User).where(User.username == settings.default_user_name))
    user = result.scalar_one_or_none()

    if user:
        return user

    user = User(
        username=settings.default_user_name,
        email=settings.default_user_email,
        display_name=settings.default_user_display_name,
        password_hash="demo-no-password",
        email_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
