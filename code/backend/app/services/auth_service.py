from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.entities import SmsVerificationCode, User, UserSession
from app.services.sms.factory import create_sms_provider

_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
_MAX_VERIFY_ATTEMPTS = 5


@dataclass(frozen=True)
class LoginResult:
    user: User
    session_token: str


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    def normalize_phone(self, phone: str) -> str:
        normalized = re.sub(r"\D", "", phone)
        if not _PHONE_RE.fullmatch(normalized):
            raise ValueError("请输入有效的中国大陆手机号")
        return normalized

    async def send_login_code(self, *, phone: str, sent_ip: str | None) -> None:
        normalized_phone = self.normalize_phone(phone)
        await self._check_send_limits(normalized_phone, sent_ip)

        code = f"{secrets.randbelow(1_000_000):06d}"
        provider = create_sms_provider()
        await provider.send_verification_code(phone=normalized_phone, code=code)

        now = datetime.now(UTC)
        record = SmsVerificationCode(
            id=uuid.uuid4(),
            phone=normalized_phone,
            scene="login",
            code_hash=self._hash_code(normalized_phone, "login", code),
            expires_at=now + timedelta(seconds=self.settings.sms_code_ttl_seconds),
            sent_ip=sent_ip,
        )
        self.session.add(record)
        await self.session.commit()

    async def login_with_code(
        self,
        *,
        phone: str,
        code: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> LoginResult:
        normalized_phone = self.normalize_phone(phone)
        clean_code = code.strip()
        if not clean_code.isdigit():
            raise ValueError("验证码格式不正确")

        now = datetime.now(UTC)
        result = await self.session.execute(
            select(SmsVerificationCode)
            .where(
                SmsVerificationCode.phone == normalized_phone,
                SmsVerificationCode.scene == "login",
                SmsVerificationCode.consumed_at.is_(None),
            )
            .order_by(desc(SmsVerificationCode.created_at))
            .limit(1)
        )
        record = result.scalar_one_or_none()
        if record is None or record.expires_at <= now:
            raise ValueError("验证码已过期，请重新获取")
        if record.attempt_count >= _MAX_VERIFY_ATTEMPTS:
            raise ValueError("验证码错误次数过多，请重新获取")

        record.attempt_count += 1
        expected_hash = self._hash_code(normalized_phone, "login", clean_code)
        if not hmac.compare_digest(record.code_hash, expected_hash):
            await self.session.commit()
            raise ValueError("验证码不正确")

        record.consumed_at = now
        user = await self._get_or_create_phone_user(normalized_phone)
        user.phone_verified = True
        user.last_login_at = now
        token = await self._create_session(user, user_agent=user_agent, ip_address=ip_address)
        await self.session.commit()
        await self.session.refresh(user)
        return LoginResult(user=user, session_token=token)

    async def get_user_by_session_token(self, token: str | None) -> User | None:
        if not token:
            return None
        now = datetime.now(UTC)
        token_hash = self.hash_session_token(token)
        result = await self.session.execute(
            select(UserSession).where(
                UserSession.session_token_hash == token_hash,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
        )
        session_record = result.scalar_one_or_none()
        if session_record is None:
            return None
        user = await self.session.get(User, session_record.user_id)
        if user is None:
            return None
        session_record.last_seen_at = now
        await self.session.commit()
        return user

    async def revoke_session_token(self, token: str | None) -> None:
        if not token:
            return
        result = await self.session.execute(
            select(UserSession).where(UserSession.session_token_hash == self.hash_session_token(token))
        )
        session_record = result.scalar_one_or_none()
        if session_record is not None and session_record.revoked_at is None:
            session_record.revoked_at = datetime.now(UTC)
            await self.session.commit()

    async def set_password(self, user: User, password: str) -> User:
        user.password_hash = self.hash_password(password)
        user.password_set_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    def hash_password(self, password: str) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
        return f"pbkdf2_sha256$200000${salt}${digest.hex()}"

    def hash_session_token(self, token: str) -> str:
        return hmac.new(self.settings.auth_secret_key.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()

    async def _check_send_limits(self, phone: str, sent_ip: str | None) -> None:
        now = datetime.now(UTC)
        cooldown_since = now - timedelta(seconds=self.settings.sms_send_cooldown_seconds)
        recent_result = await self.session.execute(
            select(SmsVerificationCode.id)
            .where(SmsVerificationCode.phone == phone, SmsVerificationCode.created_at >= cooldown_since)
            .limit(1)
        )
        if recent_result.scalar_one_or_none() is not None:
            raise ValueError("验证码发送太频繁，请稍后再试")

        day_start = now - timedelta(hours=24)
        phone_count = await self._count_codes(SmsVerificationCode.phone == phone, day_start)
        if phone_count >= self.settings.sms_daily_limit_per_phone:
            raise ValueError("该手机号今日验证码发送次数已达上限")

        if sent_ip:
            ip_count = await self._count_codes(SmsVerificationCode.sent_ip == sent_ip, day_start)
            if ip_count >= self.settings.sms_daily_limit_per_ip:
                raise ValueError("当前网络今日验证码发送次数已达上限")

    async def _count_codes(self, condition: object, since: datetime) -> int:
        result = await self.session.execute(
            select(func.count(SmsVerificationCode.id)).where(condition, SmsVerificationCode.created_at >= since)
        )
        return int(result.scalar_one())

    def _hash_code(self, phone: str, scene: str, code: str) -> str:
        payload = f"{phone}:{scene}:{code}"
        return hmac.new(self.settings.auth_secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

    async def _get_or_create_phone_user(self, phone: str) -> User:
        result = await self.session.execute(select(User).where(User.phone == phone))
        user = result.scalar_one_or_none()
        if user is not None:
            return user

        user = User(
            id=uuid.uuid4(),
            username=f"phone_{phone}",
            phone=phone,
            display_name=_mask_phone(phone),
            phone_verified=True,
            email_verified=False,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def _create_session(self, user: User, *, user_agent: str | None, ip_address: str | None) -> str:
        token = secrets.token_urlsafe(48)
        record = UserSession(
            id=uuid.uuid4(),
            session_token_hash=self.hash_session_token(token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.settings.auth_session_ttl_seconds),
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self.session.add(record)
        await self.session.flush()
        return token


def _mask_phone(phone: str) -> str:
    return f"{phone[:3]}****{phone[-4:]}"
