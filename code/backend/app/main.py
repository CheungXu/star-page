from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
from app.core.executors import install_default_executor
from app.services.billing.errors import AnonLimitError
from app.services.generation_bus import CHANNEL_BATCH, GenerationBus
from app.services.generation_runner import generation_runner

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if settings.app_role == "worker":
        raise RuntimeError("APP_ROLE=worker 的进程用 python -m app.worker 启动，不要用 uvicorn")
    install_default_executor()
    bus: GenerationBus | None = None
    if settings.app_role == "web":
        # web 不跑生成也不扫尾：生成在 worker 里，事件流靠监听 worker 发来的批次通知唤醒。
        # 连上和断开时都唤醒全部订阅者：连上时补读断线期间漏掉的事件，断开时让它们马上改成定时读库。
        bus = GenerationBus()
        bus.subscribe(CHANNEL_BATCH, generation_runner.handle_batch_notice)
        bus.on_connect(generation_runner.wake_all)
        bus.on_disconnect(generation_runner.wake_all)
        generation_runner.attach_bus(bus)
        await bus.start()
    elif settings.generation_startup_sweep:
        # 单进程：上一个进程留下的 pending/running 任务，近期中断的从头重跑一次，其余标失败。
        # 共用线上库的预览实例要设 GENERATION_STARTUP_SWEEP=false。
        try:
            await generation_runner.startup_sweep()
        except Exception as exc:  # noqa: BLE001 - 扫尾失败不阻止服务启动
            print(f"[generation] 启动扫尾失败：{exc!r}", flush=True)
    yield
    if bus is not None:
        await bus.stop()
    # 退出时只取消协程、不改状态，留给下次启动扫尾。
    await generation_runner.shutdown()


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.exception_handler(AnonLimitError)
async def anon_limit_handler(_request: Request, exc: AnonLimitError) -> JSONResponse:
    """新建匿名身份时同一网络当天已超上限：发生在任何接口解析操作者的那一步，统一提示登录，而不是 500。"""
    return JSONResponse(status_code=status.HTTP_402_PAYMENT_REQUIRED, content={"detail": exc.to_detail(need_login=True)})

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
