"""把用户上传的图片存进对象存储，供生成页通过 /assets/{id} 引用。

两个入口：场景槽位图（头像、二维码等，有角色名）和通用上传口的图片（与文档一起提交）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from starlette.datastructures import UploadFile

from app.services.document_parser.images import image_size, sniff_image
from app.services.document_parser.types import UploadedImage
from app.services.storage.factory import create_storage_provider

MAX_IMAGE_COUNT = 3
MAX_IMAGE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class StoredImage:
    asset_id: uuid.UUID
    storage_key: str
    content_type: str
    filename: str
    role: str
    byte_size: int
    source_kind: str = "scene_slot"
    width: int | None = None
    height: int | None = None


async def store_uploaded_images(
    files: list[UploadFile],
    roles: list[str],
    slot_labels: dict[str, str],
) -> list[StoredImage]:
    """校验并保存最多 3 张场景槽位图。角色名优先用调用方传入的 image_roles。"""
    if len(files) > MAX_IMAGE_COUNT:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"最多上传 {MAX_IMAGE_COUNT} 张图片")

    prepared: list[tuple[bytes, str, str, str, str, int | None, int | None]] = []
    for index, upload in enumerate(files):
        data = await upload.read()
        if not data:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="有一张图片是空的，请重新选择")
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="单张图片不能超过 2MB")
        detected = sniff_image(data)
        if detected is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="图片仅支持 PNG、JPEG、WEBP、GIF")
        content_type, extension = detected
        try:
            width, height = image_size(data)
        except Exception:  # noqa: BLE001 - 尺寸只用于提示，读不到不拦截
            width = height = None
        role = roles[index].strip() if index < len(roles) and roles[index].strip() else "image"
        filename = (upload.filename or "image").rsplit("/", 1)[-1][:200]
        prepared.append((data, content_type, extension, role[:32], filename, width, height))

    return await _store(
        [(data, content_type, extension, role, filename, "scene_slot", width, height) for data, content_type, extension, role, filename, width, height in prepared]
    )


async def store_general_images(images: list[UploadedImage]) -> list[StoredImage]:
    """通用上传口的图片：已在预检里校验过格式、大小和像素。"""
    return await _store(
        [(image.data, image.content_type, image.extension, "image", image.filename, "user_upload", image.width, image.height) for image in images]
    )


async def _store(items: list[tuple[bytes, str, str, str, str, str, int | None, int | None]]) -> list[StoredImage]:
    storage = create_storage_provider()
    stored: list[StoredImage] = []
    try:
        for data, content_type, extension, role, filename, source_kind, width, height in items:
            asset_id = uuid.uuid4()
            storage_key = f"page-assets/{asset_id}{extension}"
            await storage.put_bytes(storage_key, data, content_type)
            stored.append(
                StoredImage(
                    asset_id=asset_id,
                    storage_key=storage_key,
                    content_type=content_type,
                    filename=filename,
                    role=role,
                    byte_size=len(data),
                    source_kind=source_kind,
                    width=width,
                    height=height,
                )
            )
    except Exception:
        await discard_stored_images(stored)
        raise
    return stored


async def discard_stored_images(images: list[StoredImage]) -> None:
    """生成没创建成功时，删掉已经写入存储的图片。"""
    if not images:
        return
    storage = create_storage_provider()
    for image in images:
        try:
            await storage.delete(image.storage_key)
        except Exception:
            continue
