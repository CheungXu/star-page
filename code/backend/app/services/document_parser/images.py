"""图片识别、归一化与转 data URL。"""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps

from app.services.document_parser.limits import MAX_IMAGE_PIXELS, MIN_IMAGE_SIDE, STORED_IMAGE_MAX_EDGE

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

WEB_FORMATS = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg"), "WEBP": ("image/webp", ".webp"), "GIF": ("image/gif", ".gif")}


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    content_type: str
    extension: str
    width: int
    height: int
    sha256: str


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """按文件头判断图片格式，返回 (content_type, extension)。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


_ROTATED_ORIENTATIONS = {5, 6, 7, 8}


def _display_size(image: Image.Image) -> tuple[int, int]:
    """按 EXIF 方向换算成显示时的宽高。手机竖拍常存成横向像素加「旋转 90°」标记，浏览器按标记显示。"""
    width, height = image.size
    try:
        orientation = image.getexif().get(0x0112)
    except Exception:  # noqa: BLE001 - EXIF 损坏时按原始尺寸
        orientation = None
    return (height, width) if orientation in _ROTATED_ORIENTATIONS else (width, height)


def image_size(data: bytes) -> tuple[int, int]:
    """读显示宽高并校验像素上限。像素炸弹会抛 Image.DecompressionBombError。"""
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise Image.DecompressionBombError("图片像素过大")
        image.verify()
    # verify 之后对象不能再用，读 EXIF 要重新打开。
    with Image.open(io.BytesIO(data)) as image:
        return _display_size(image)


def normalize_image(data: bytes, *, max_edge: int = STORED_IMAGE_MAX_EDGE) -> NormalizedImage | None:
    """把文档里取出的图转成网页可用的格式。太小、打不开、像素过大的返回 None。"""
    digest = hashlib.sha256(data).hexdigest()
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                return None
            if min(image.size) < MIN_IMAGE_SIDE:
                return None
            fmt = (image.format or "").upper()
            if fmt in WEB_FORMATS and max(image.size) <= max_edge:
                content_type, extension = WEB_FORMATS[fmt]
                width, height = _display_size(image)
                return NormalizedImage(data, content_type, extension, width, height, digest)
            return _reencode(image, max_edge, digest)
    except (Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        return None


def normalize_pil(image: Image.Image, *, max_edge: int = STORED_IMAGE_MAX_EDGE) -> NormalizedImage | None:
    """pdfium 渲染出的 PIL 图直接编码。"""
    if min(image.size) < MIN_IMAGE_SIDE or image.width * image.height > MAX_IMAGE_PIXELS:
        return None
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return _reencode(image, max_edge, hashlib.sha256(buffer.getvalue()).hexdigest())


def _reencode(image: Image.Image, max_edge: int, digest: str) -> NormalizedImage:
    image = ImageOps.exif_transpose(image)
    if max(image.size) > max_edge:
        image = image.copy()
        image.thumbnail((max_edge, max_edge))
    has_alpha = image.mode in {"RGBA", "LA", "PA"} or (image.mode == "P" and "transparency" in image.info)
    buffer = io.BytesIO()
    if has_alpha:
        image.convert("RGBA").save(buffer, format="PNG", optimize=True)
        content_type, extension = "image/png", ".png"
    else:
        image.convert("RGB").save(buffer, format="JPEG", quality=88, optimize=True)
        content_type, extension = "image/jpeg", ".jpg"
    return NormalizedImage(buffer.getvalue(), content_type, extension, image.width, image.height, digest)


def to_data_url(data: bytes, *, max_edge: int = 1536) -> str | None:
    """缩到长边不超过 max_edge 后转 data URL，只用于发给模型。"""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.seek(0)
            frame = ImageOps.exif_transpose(image.convert("RGBA") if image.mode in {"P", "LA"} else image)
            if max(frame.size) > max_edge:
                frame = frame.copy()
                frame.thumbnail((max_edge, max_edge))
            buffer = io.BytesIO()
            if frame.mode in {"RGBA", "LA"}:
                frame.save(buffer, format="PNG", optimize=True)
                mime = "image/png"
            else:
                frame.convert("RGB").save(buffer, format="JPEG", quality=85)
                mime = "image/jpeg"
    except (Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        return None
    return f"data:{mime};base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
