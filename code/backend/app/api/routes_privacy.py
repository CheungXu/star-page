"""数据与隐私：训练授权开关、老用户协议更新告知。"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.core.auth import get_client_ip, get_current_user, get_optional_actor, resolve_actor
from app.core.config import get_billing_config
from app.core.database import AsyncSessionLocal
from app.models.entities import User
from app.services.consent_service import POLICY_VERSION, ConsentService, needs_policy_notice

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


class PrivacyPreferences(BaseModel):
    allow_training: bool
    is_anonymous: bool
    needs_policy_notice: bool
    training_discount_rate: float
    policy_version: str


class PrivacyPreferencesUpdate(BaseModel):
    allow_training: bool


def _to_response(user: User | None) -> PrivacyPreferences:
    return PrivacyPreferences(
        allow_training=user.allow_training if user is not None else True,
        is_anonymous=user is None or user.is_anonymous,
        needs_policy_notice=needs_policy_notice(user) if user is not None else False,
        training_discount_rate=get_billing_config().training_discount_rate,
        policy_version=POLICY_VERSION,
    )


@router.get("/preferences", response_model=PrivacyPreferences)
async def get_preferences(request: Request) -> PrivacyPreferences:
    async with AsyncSessionLocal() as session:
        return _to_response(await get_optional_actor(session, request))


@router.put("/preferences", response_model=PrivacyPreferences)
async def update_preferences(payload: PrivacyPreferencesUpdate, request: Request, response: Response) -> PrivacyPreferences:
    """匿名访客也能关闭授权：没有匿名身份时先建一个，选择才有地方保存。"""
    async with AsyncSessionLocal() as session:
        user = await resolve_actor(session, request, response)
        ConsentService(session).set_allow_training(
            user,
            payload.allow_training,
            ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        await session.commit()
        await session.refresh(user)
        return _to_response(user)


@router.post("/notice-ack", response_model=PrivacyPreferences)
async def acknowledge_notice(request: Request) -> PrivacyPreferences:
    async with AsyncSessionLocal() as session:
        user = await get_current_user(session, request)
        ConsentService(session).acknowledge_notice(
            user, ip=get_client_ip(request), user_agent=request.headers.get("user-agent")
        )
        await session.commit()
        await session.refresh(user)
        return _to_response(user)
