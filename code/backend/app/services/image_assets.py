"""把用户上传的图片存进对象存储，供生成页通过 /assets/{id} 引用。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from starlette.datastructures import UploadFile

from app.services.storage.factory import create_storage_provider

MAX_IMAGE_COUNT = 3
MAX_IMAGE_BYTES = 2 * 1024 * 1024
_ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


@dataclass(frozen=True)
class StoredImage:
    asset_id: uuid.UUID
    storage_key: str
    content_type: str
    filename: str
    role: str
    byte_size: int


async def store_uploaded_images(
    files: list[UploadFile],
    roles: list[str],
    slot_labels: dict[str, str],
) -> list[StoredImage]:
    """校验并保存最多 3 张图片。角色名优先用调用方传入的 image_roles。"""
    if len(files) > MAX_IMAGE_COUNT:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"最多上传 {MAX_IMAGE_COUNT} 张图片")

    prepared: list[tuple[bytes, str, str, str, str]] = []
    for index, upload in enumerate(files):
        data = await upload.read()
        if not data:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="有一张图片是空的，请重新选择")
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="单张图片不能超过 2MB")
        detected = _detect_image(data, (upload.content_type or "").split(";")[0].strip().lower())
        if detected is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="图片仅支持 PNG、JPEG、WEBP、GIF")
        content_type, extension = detected
        role = roles[index].strip() if index < len(roles) and roles[index].strip() else "image"
        filename = (upload.filename or "image").rsplit("/", 1)[-1][:200]
        prepared.append((data, content_type, extension, role[:32], filename))

    storage = create_storage_provider()
    stored: list[StoredImage] = []
    try:
        for data, content_type, extension, role, filename in prepared:
            asset_id = uuid.uuid4()
            storage_key = f"page-assets/{asset_id}{extension}"
            await storage.put_bytes(storage_key, data, content_type)
            stored.append(
                StoredImage(
                    asset_id=asset_id,
                    storage_key=storage_key,
                    content_type=content_type,
                    filename=filename,
                    role=role if role in slot_labels or role == "image" else role,
                    byte_size=len(data),
                )
            )
    except Exception:
        await discard_stored_images(stored)
        raise
    return stored


def _detect_image(data: bytes, content_type: str) -> tuple[str, str] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if content_type == "image/jpg":
        content_type = "image/jpeg"
    extension = _ALLOWED_TYPES.get(content_type)
    if extension is None:
        return None
    return content_type, extension


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
