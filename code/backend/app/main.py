from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_admin import router as admin_router
from app.api.routes_admin_analytics import router as admin_analytics_router
from app.api.routes_analytics import router as analytics_router
from app.api.routes_auth import router as auth_router
from app.api.routes_billing import router as billing_router
from app.api.routes_conversations import router as conversations_router
from app.api.routes_generation import router as generation_router
from app.api.routes_pages import router as pages_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name)

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
app.include_router(billing_router)
app.include_router(admin_router)
app.include_router(analytics_router)
app.include_router(admin_analytics_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
