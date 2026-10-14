"""训练授权：生成时的授权快照、同意记录、开关变更与老用户告知。

授权快照取值：
- granted：生成时已授权，享受授权折扣，生成完成时当场提取匿名结构样本；
- denied：生成时未授权，按标准价计费，不用于训练；
- legacy：协议上线前生成，或老用户尚未看到更新告知，暂不使用。

一条原始数据能否进入新的训练 = 快照为 granted 且用户当前仍授权（撤回后停用）。
匿名结构样本不含个人信息，不受撤回影响。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import User, UserConsent
from app.services.analytics.tracking import hash_ip

# 协议版本：协议文本有实质修改时更新，前端登录勾选时回传同一版本。
POLICY_VERSION = "2026-10-13"

CONSENT_GRANTED = "granted"
CONSENT_DENIED = "denied"
CONSENT_LEGACY = "legacy"


def training_consent_for(user: User | None) -> str:
    """返回该用户此刻生成数据的授权快照。"""
    if user is None:
        # 没有身份的访问只在页脚「使用即同意」下发生，无法关闭也无法关联到个人。
        return CONSENT_GRANTED
    if not user.allow_training:
        return CONSENT_DENIED
    if user.is_anonymous:
        return CONSENT_GRANTED
    if user.policy_notice_seen_at is None:
        return CONSENT_LEGACY
    return CONSENT_GRANTED


def needs_policy_notice(user: User) -> bool:
    """登录用户还没在新版协议下同意或确认过告知（协议上线前注册的老用户）。"""
    return not user.is_anonymous and user.policy_notice_seen_at is None


class ConsentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def record(
        self,
        user: User,
        *,
        event: str,
        ip: str | None,
        user_agent: str | None,
        policy_version: str = POLICY_VERSION,
    ) -> None:
        self.session.add(
            UserConsent(
                id=uuid.uuid4(),
                user_id=user.id,
                event=event,
                policy_version=policy_version[:32],
                allow_training=bool(user.allow_training),
                ip_hash=hash_ip(ip),
                user_agent=(user_agent or "")[:2000] or None,
            )
        )

    def agree_on_login(self, user: User, *, policy_version: str, ip: str | None, user_agent: str | None) -> None:
        """登录弹窗勾选同意：视为在新版协议下同意，老用户也不再需要告知。"""
        user.policy_notice_seen_at = user.policy_notice_seen_at or datetime.now(UTC)
        self.record(user, event="login_agree", ip=ip, user_agent=user_agent, policy_version=policy_version)

    def acknowledge_notice(self, user: User, *, ip: str | None, user_agent: str | None) -> bool:
        """老用户确认协议更新告知。重复调用不重复记录，返回本次是否新记录。"""
        if user.is_anonymous or user.policy_notice_seen_at is not None:
            return False
        user.policy_notice_seen_at = datetime.now(UTC)
        self.record(user, event="notice_ack", ip=ip, user_agent=user_agent)
        return True

    def set_allow_training(self, user: User, allow: bool, *, ip: str | None, user_agent: str | None) -> bool:
        """修改授权开关，只影响之后的生成。返回是否发生变化。"""
        if user.allow_training == allow:
            return False
        user.allow_training = allow
        user.training_updated_at = datetime.now(UTC)
        self.record(user, event="preference", ip=ip, user_agent=user_agent)
        return True

    def carry_anon_opt_out(self, anon_user: User, target: User, *, ip: str | None, user_agent: str | None) -> None:
        """匿名期关闭过授权，登录合并后沿用更保护隐私的选择。"""
        if anon_user.allow_training or not target.allow_training:
            return
        target.allow_training = False
        target.training_updated_at = datetime.now(UTC)
        self.record(target, event="merge_opt_out", ip=ip, user_agent=user_agent)
