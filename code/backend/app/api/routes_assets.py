"""托管图片的公开读取。地址不可猜测（UUID），生成页用 img 引用。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.entities import PageAsset
from app.services.storage.factory import create_storage_provider

router = APIRouter(tags=["assets"])


@router.get("/assets/{asset_id}")
async def serve_asset(asset_id: uuid.UUID) -> Response:
    async with AsyncSessionLocal() as session:
        asset = await session.scalar(select(PageAsset).where(PageAsset.id == asset_id))
        if asset is None:
            raise HTTPException(status_code=404, detail="图片不存在")
        storage_key = asset.storage_key
        content_type = asset.content_type

    storage = create_storage_provider()
    try:
        data = await storage.get_bytes(storage_key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="图片不存在") from exc

    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )
