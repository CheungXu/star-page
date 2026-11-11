from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_admin import router as admin_router
from app.api.routes_admin_analytics import router as admin_analytics_router
from app.api.routes_analytics import router as analytics_router
from app.api.routes_auth import router as auth_router
from app.api.routes_billing import router as billing_router
from app.api.routes_conversations import router as conversations_router
from app.api.routes_generation import router as generation_router
from app.api.routes_assets import router as assets_router
from app.api.routes_pages import router as pages_router
from app.api.routes_privacy import router as privacy_router
from app.api.routes_publications import router as publications_router
from app.api.routes_reports import router as reports_router
from app.api.routes_scenes import router as scenes_router
from app.core.config import get_settings
from app.services.generation_runner import generation_runner

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # 启动扫尾：上一个进程留下的 pending/running 任务，近期中断的从头重跑一次，其余标失败。
    # 依赖单 worker；共用线上库的预览实例要设 GENERATION_STARTUP_SWEEP=false。
    if settings.generation_startup_sweep:
        try:
            await generation_runner.startup_sweep()
        except Exception as exc:  # noqa: BLE001 - 扫尾失败不阻止服务启动
            print(f"[generation] 启动扫尾失败：{exc!r}", flush=True)
    yield
    # 退出时只取消协程、不改状态，留给下次启动扫尾。
    await generation_runner.shutdown()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(generation_router)
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(pages_router)
app.include_router(assets_router)
app.include_router(publications_router)
app.include_router(scenes_router)
app.include_router(billing_router)
app.include_router(admin_router)
app.include_router(analytics_router)
app.include_router(admin_analytics_router)
app.include_router(privacy_router)
app.include_router(reports_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
