"""运营埋点写入：生成页访问日志与前端漏斗事件。

设计要点：
- 隐私：IP 仅以 HMAC 存储（不可逆），复用 `AUTH_SECRET_KEY` 作为密钥。
- 写入均为「尽力而为」：失败只记日志，绝不影响主流程（尤其是 /p 页面访问热路径）。
- 前端上报走 event 白名单 + 进程内限频，避免被刷脏数据。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from collections import deque
from threading import Lock

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.entities import AnalyticsEvent, PageViewEvent

logger = logging.getLogger("app.analytics")

# 前端可上报的事件白名单：仅保留现有结构化表无法派生的「漏斗/意图」类事件。
# 注册成功、生成成功、充值成功等都可从 users/generation_tasks/recharge_orders 派生，无需前端上报。
ALLOWED_EVENTS: frozenset[str] = frozenset(
    {
        "landing_view",       # 落地页加载（漏斗第一步）
        "prompt_input",       # 首次在输入框输入内容（产生创作意图）
        "generate_click",     # 点击生成按钮（含被登录/付费拦截的情况）
        "login_prompt_view",  # 登录/注册引导曝光
        "login_success",      # 前端侧登录成功（便于同一会话漏斗串联）
        "recharge_view",      # 充值页/弹窗曝光
        "recharge_click",     # 点击某充值套餐
        "page_share_click",   # 点击分享生成页（传播意图）
    }
)

# 限频：同一 IP 每分钟最多上报的事件数，进程内滑动窗口（单 worker 部署足够）。
_RATE_WINDOW_SECONDS = 60.0
_RATE_MAX_EVENTS = 120
_MAX_TRACKED_KEYS = 10000
_rate_buckets: dict[str, deque[float]] = {}
_rate_lock = Lock()


def hash_ip(ip: str | None) -> str | None:
    """对 IP 做 HMAC-SHA256，返回 64 位十六进制摘要；为空则返回 None。"""
    if not ip:
        return None
    secret = get_settings().auth_secret_key.encode("utf-8")
    return hmac.new(secret, ip.encode("utf-8"), hashlib.sha256).hexdigest()


def allow_event(ip_hash: str | None) -> bool:
    """进程内滑动窗口限频：按 ip_hash 计数，超过阈值则丢弃。无 ip 时不限频。"""
    if ip_hash is None:
        return True
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets.get(ip_hash)
        if bucket is None:
            # 防止 key 无限增长：超过上限时清空（极端情况下退化为不限频，可接受）。
            if len(_rate_buckets) >= _MAX_TRACKED_KEYS:
                _rate_buckets.clear()
            bucket = deque()
            _rate_buckets[ip_hash] = bucket
        cutoff = now - _RATE_WINDOW_SECONDS
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _RATE_MAX_EVENTS:
            return False
        bucket.append(now)
        return True


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value[:limit]


async def record_page_view(
    *,
    page_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    owner_user_id: uuid.UUID | None,
    viewer_user_id: uuid.UUID | None,
    is_owner_view: bool,
    ip: str | None,
    referer: str | None,
    user_agent: str | None,
) -> None:
    """写一条生成页访问日志。供 /p 网关以 BackgroundTask 调用，失败不抛出。"""
    try:
        async with AsyncSessionLocal() as session:
            session.add(
                PageViewEvent(
                    id=uuid.uuid4(),
                    page_id=page_id,
                    conversation_id=conversation_id,
                    owner_user_id=owner_user_id,
                    viewer_user_id=viewer_user_id,
                    is_owner_view=is_owner_view,
                    ip_hash=hash_ip(ip),
                    referer=_clip(referer, 2000),
                    user_agent=_clip(user_agent, 2000),
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - 埋点绝不影响页面访问
        logger.warning("记录生成页访问日志失败 page_id=%s", page_id, exc_info=True)


async def record_analytics_event(
    *,
    event_name: str,
    user_id: uuid.UUID | None,
    anon_device_id: str | None,
    client_session_id: str | None,
    props: dict | None,
    ip: str | None,
    referer: str | None,
    user_agent: str | None,
) -> None:
    """写一条前端埋点事件，失败不抛出。"""
    try:
        async with AsyncSessionLocal() as session:
            session.add(
                AnalyticsEvent(
                    id=uuid.uuid4(),
                    event_name=event_name,
                    user_id=user_id,
                    anon_device_id=_clip(anon_device_id, 64),
                    client_session_id=_clip(client_session_id, 64),
                    props=props or {},
                    ip_hash=hash_ip(ip),
                    referer=_clip(referer, 2000),
                    user_agent=_clip(user_agent, 2000),
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("记录前端埋点事件失败 event=%s", event_name, exc_info=True)
