from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class SmsSendRequest(BaseModel):
    phone: str = Field(min_length=11, max_length=20)


class SmsSendResponse(BaseModel):
    ok: bool = True
    cooldown_seconds: int


class SmsLoginRequest(BaseModel):
    phone: str = Field(min_length=11, max_length=20)
    code: str = Field(min_length=4, max_length=8)


class AuthUserResponse(BaseModel):
    id: UUID
    phone: str
    display_name: str
    phone_verified: bool
    has_password: bool
    is_admin: bool = False


class AuthLoginResponse(BaseModel):
    user: AuthUserResponse


class PasswordSetRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)
