from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core.auth import (
    clear_anon_cookie,
    get_client_ip,
    get_current_user,
    get_optional_actor,
    get_session_token,
    is_admin_user,
)
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.schemas.auth import (
    AuthLoginResponse,
    AuthUserResponse,
    PasswordSetRequest,
    SmsLoginRequest,
    SmsSendRequest,
    SmsSendResponse,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/sms/send", response_model=SmsSendResponse)
async def send_sms_code(payload: SmsSendRequest, request: Request) -> SmsSendResponse:
    async with AsyncSessionLocal() as session:
        service = AuthService(session)
        try:
            await service.send_login_code(phone=payload.phone, sent_ip=get_client_ip(request))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return SmsSendResponse(cooldown_seconds=service.settings.sms_send_cooldown_seconds)


@router.post("/sms/login", response_model=AuthLoginResponse)
async def login_with_sms(payload: SmsLoginRequest, request: Request, response: Response) -> AuthLoginResponse:
    async with AsyncSessionLocal() as session:
        service = AuthService(session)
        anon_user = await get_optional_actor(session, request)
        anon_user = anon_user if (anon_user and anon_user.is_anonymous) else None
        try:
            result = await service.login_with_code(
                phone=payload.phone,
                code=payload.code,
                user_agent=request.headers.get("user-agent"),
                ip_address=get_client_ip(request),
                anon_user=anon_user,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

        _set_session_cookie(response, result.session_token)
        clear_anon_cookie(response)
        return AuthLoginResponse(user=await _to_user_response(session, result.user))


@router.get("/me", response_model=AuthUserResponse)
async def get_me(request: Request) -> AuthUserResponse:
    async with AsyncSessionLocal() as session:
        user = await get_current_user(session, request)
        return await _to_user_response(session, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> None:
    async with AsyncSessionLocal() as session:
        await AuthService(session).revoke_session_token(get_session_token(request))
    _clear_session_cookie(response)


@router.post("/password", response_model=AuthUserResponse)
async def set_password(payload: PasswordSetRequest, request: Request) -> AuthUserResponse:
    async with AsyncSessionLocal() as session:
        user = await get_current_user(session, request)
        user = await AuthService(session).set_password(user, payload.password)
        return await _to_user_response(session, user)


async def _to_user_response(session, user) -> AuthUserResponse:
    return AuthUserResponse(
        id=user.id,
        phone=user.phone or "",
        display_name=user.display_name,
        phone_verified=user.phone_verified,
        has_password=bool(user.password_hash),
        is_admin=await is_admin_user(session, user),
    )


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=token,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.auth_session_cookie_name,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
