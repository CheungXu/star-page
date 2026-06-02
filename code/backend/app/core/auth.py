from __future__ import annotations

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.entities import User
from app.services.auth_service import AuthService


def get_session_token(request: Request) -> str | None:
    return request.cookies.get(get_settings().auth_session_cookie_name)


async def get_optional_user(session: AsyncSession, request: Request) -> User | None:
    token = get_session_token(request)
    return await AuthService(session).get_user_by_session_token(token)


async def get_current_user(session: AsyncSession, request: Request) -> User:
    user = await get_optional_user(session, request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return user


def get_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or None
    return request.client.host if request.client else None
