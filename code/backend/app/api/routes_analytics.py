"""运营埋点上报接口（公开，允许匿名）。

管理端运营指标接口在 routes_admin_analytics.py（需要管理员权限）。
本文件只承载前端埋点的写入：event 白名单校验 + 进程内限频，再异步落库。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from app.core.auth import get_client_ip, get_optional_actor
from app.core.database import AsyncSessionLocal
from app.schemas.analytics import AnalyticsCollectRequest
from app.services.analytics import record_analytics_event
from app.services.analytics.tracking import ALLOWED_EVENTS, allow_event, hash_ip

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.post("/collect", status_code=status.HTTP_204_NO_CONTENT)
async def collect(payload: AnalyticsCollectRequest, request: Request, background_tasks: BackgroundTasks) -> Response:
    """接收前端漏斗埋点。非白名单事件、超频请求一律静默丢弃（始终回 204，避免暴露校验细节）。"""
    event = payload.event.strip()
    if event not in ALLOWED_EVENTS:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    ip = get_client_ip(request)
    if not allow_event(hash_ip(ip)):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # 只读解析操作者：登录用户或已有 cookie 的匿名用户，绝不在埋点接口里创建匿名账号。
    user_id = None
    anon_device_id = None
    async with AsyncSessionLocal() as session:
        actor = await get_optional_actor(session, request)
        if actor is not None:
            if actor.is_anonymous:
                anon_device_id = actor.anon_device_id
            else:
                user_id = actor.id

    # props 只接受浅层、可序列化的小对象，做一次大小裁剪防滥用。
    props = payload.props if isinstance(payload.props, dict) else {}
    if len(props) > 30:
        props = dict(list(props.items())[:30])

    background_tasks.add_task(
        record_analytics_event,
        event_name=event,
        user_id=user_id,
        anon_device_id=anon_device_id,
        client_session_id=payload.client_session_id,
        props=props,
        ip=ip,
        referer=request.headers.get("referer"),
        user_agent=request.headers.get("user-agent"),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
