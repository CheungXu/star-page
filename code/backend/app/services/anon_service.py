from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_billing_config, get_settings
from app.models.entities import AnonVisitor, User
from app.services.billing.errors import AnonLimitError


class AnonService:
    """匿名访客：签名 cookie 防伪造、按 IP 每日签发数天花板防批量薅，懒创建 is_anonymous 用户。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()
        self.config = get_billing_config()

    # ---------- 签名 cookie ----------

    def sign_device_id(self, device_id: str) -> str:
        sig = self._signature(device_id)
        return f"{device_id}.{sig}"

    def verify_cookie(self, raw: str | None) -> str | None:
        """校验签名 cookie，返回合法 device_id；伪造/篡改返回 None。"""
        if not raw or "." not in raw:
            return None
        device_id, _, sig = raw.rpartition(".")
        if not device_id or not sig:
            return None
        expected = self._signature(device_id)
        if not hmac.compare_digest(sig, expected):
            return None
        return device_id

    def _signature(self, device_id: str) -> str:
        return hmac.new(
            self.settings.auth_secret_key.encode("utf-8"),
            f"anon:{device_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]

    # ---------- 解析 / 懒创建 ----------

    async def get_existing(self, raw_cookie: str | None) -> User | None:
        device_id = self.verify_cookie(raw_cookie)
        if not device_id:
            return None
        result = await self.session.execute(select(AnonVisitor).where(AnonVisitor.anon_device_id == device_id))
        visitor = result.scalar_one_or_none()
        if visitor is None:
            return None
        user = await self.session.get(User, visitor.user_id)
        if user is None or not user.is_anonymous or user.merged_into_user_id is not None:
            return None
        return user

    async def create_visitor(self, *, sign_ip: str | None, user_agent: str | None) -> tuple[User, str]:
        """通过 IP 天花板后创建匿名用户与访客记录，返回 (user, 签名cookie值)。"""
        await self._check_ip_issue_ceiling(sign_ip)

        device_id = uuid.uuid4().hex
        user = User(
            id=uuid.uuid4(),
            username=f"anon_{device_id}",
            display_name="访客",
            is_anonymous=True,
            anon_device_id=device_id,
            password_hash=None,
            email_verified=False,
            phone_verified=False,
        )
        self.session.add(user)
        await self.session.flush()

        visitor = AnonVisitor(
            id=uuid.uuid4(),
            anon_device_id=device_id,
            user_id=user.id,
            sign_ip=sign_ip,
            user_agent=user_agent,
        )
        self.session.add(visitor)
        await self.session.flush()
        return user, self.sign_device_id(device_id)

    async def _check_ip_issue_ceiling(self, sign_ip: str | None) -> None:
        if not sign_ip:
            return
        since = datetime.now(UTC) - timedelta(hours=24)
        result = await self.session.execute(
            select(func.count(AnonVisitor.id)).where(
                AnonVisitor.sign_ip == sign_ip, AnonVisitor.created_at >= since
            )
        )
        count = int(result.scalar_one())
        if count >= self.config.anon_daily_id_limit_per_ip:
            raise AnonLimitError("当前网络匿名访问过于频繁，请登录后继续")

    async def check_ip_free_generation_ceiling(self, sign_ip: str | None) -> None:
        """按 IP 限制每日匿名免费生成次数（即使清 cookie 也封住批量薅）。"""
        if not sign_ip:
            return
        since = datetime.now(UTC) - timedelta(hours=24)
        result = await self.session.execute(
            select(func.coalesce(func.sum(AnonVisitor.free_generations_used), 0)).where(
                AnonVisitor.sign_ip == sign_ip, AnonVisitor.created_at >= since
            )
        )
        used = int(result.scalar_one())
        if used >= self.config.anon_daily_free_generation_limit_per_ip:
            raise AnonLimitError("当前网络免费体验次数已达上限，请登录后继续")
