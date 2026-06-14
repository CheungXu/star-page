from __future__ import annotations

from fastapi import HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.entities import User
from app.services.anon_service import AnonService
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


def is_admin_user(user: User) -> bool:
    from app.core.config import get_billing_config

    phones = set(get_billing_config().admin_phones)
    return bool(user.phone and user.phone in phones)


async def require_admin(session: AsyncSession, request: Request) -> User:
    user = await get_current_user(session, request)
    if not is_admin_user(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


async def resolve_actor(session: AsyncSession, request: Request, response: Response) -> User:
    """返回当前操作者：已登录则登录用户；否则按签名 cookie 取/懒建匿名用户（并回写 cookie）。"""
    user = await get_optional_user(session, request)
    if user is not None:
        return user

    settings = get_settings()
    anon = AnonService(session)
    raw_cookie = request.cookies.get(settings.anon_cookie_name)
    existing = await anon.get_existing(raw_cookie)
    if existing is not None:
        return existing

    new_user, signed_value = await anon.create_visitor(
        sign_ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()
    set_anon_cookie(response, signed_value)
    return new_user


async def get_optional_actor(session: AsyncSession, request: Request) -> User | None:
    """只读解析操作者：登录用户或已有签名 cookie 的匿名用户；不创建、不写 cookie。"""
    user = await get_optional_user(session, request)
    if user is not None:
        return user
    anon = AnonService(session)
    raw_cookie = request.cookies.get(get_settings().anon_cookie_name)
    return await anon.get_existing(raw_cookie)


def set_anon_cookie(response: Response, signed_value: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.anon_cookie_name,
        value=signed_value,
        max_age=settings.anon_cookie_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_anon_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.anon_cookie_name,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def get_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or None
    return request.client.host if request.client else None
