"""创建请求里的同步预检：坏文件、超限文件直接 4xx，不建批次、不扣免费次数。

只做便宜的检查（文件头、能否打开、是否加密、页数、zip 解压规模），完整解析在后台做。
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from fastapi import HTTPException, status
from PIL import Image

from app.services.document_parser.images import image_size, sniff_image
from app.services.document_parser.limits import (
    DOCUMENT_EXTENSIONS,
    DOCX_MAX_BLOCKS,
    IMAGE_EXTENSIONS,
    MAX_FILE_COUNT,
    MAX_FILE_SIZE_BYTES,
    MAX_TOTAL_FILE_SIZE_BYTES,
    MAX_UPLOAD_IMAGE_BYTES,
    PDF_MAX_PAGES,
    PPTX_MAX_SLIDES,
    SUPPORTED_EXTENSIONS,
    SUPPORTED_FILE_TYPES_LABEL,
    ZIP_MAX_ENTRIES,
    ZIP_MAX_UNCOMPRESSED_BYTES,
)
from app.services.document_parser.types import StagedDocument, UploadedImage

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK\x03\x04"
_ZIP_KINDS = {".docx": "word/document.xml", ".pptx": "ppt/presentation.xml", ".xlsx": "xl/workbook.xml"}
_SLIDE_ENTRY = re.compile(r"^ppt/slides/slide\d+\.xml$")


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _too_large(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=detail)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def precheck_uploads(files: list[tuple[str, bytes]]) -> tuple[list[StagedDocument], list[UploadedImage]]:
    """files 为 (文件名, 字节)。返回通过预检的文档和图片。CPU 较重，调用方放进线程里跑。"""
    selected = [(Path(name or "未命名文件").name, data) for name, data in files if name]
    if len(selected) > MAX_FILE_COUNT:
        raise _too_large(f"当前一次最多上传 {MAX_FILE_COUNT} 个文件")

    documents: list[StagedDocument] = []
    images: list[UploadedImage] = []
    total = 0
    for filename, data in selected:
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise _bad_request(f"{filename} 的格式暂不支持，请上传 {SUPPORTED_FILE_TYPES_LABEL} 文件")
        if not data:
            raise _unprocessable(f"{filename} 是空文件，请重新选择")
        size = len(data)
        total += size
        if extension in IMAGE_EXTENSIONS and size > MAX_UPLOAD_IMAGE_BYTES:
            raise _too_large(f"{filename} 超过 10MB，请压缩后再上传")
        if size > MAX_FILE_SIZE_BYTES:
            raise _too_large(f"{filename} 超过 50MB，请压缩或拆分后再上传")
        if total > MAX_TOTAL_FILE_SIZE_BYTES:
            raise _too_large("本次上传文件总大小超过 50MB，请减少文件大小")

        if extension in IMAGE_EXTENSIONS:
            images.append(_check_image(filename, data))
        elif extension in DOCUMENT_EXTENSIONS:
            _check_document(filename, extension, data)
            documents.append(StagedDocument(filename=filename, extension=extension, data=data))
    return documents, images


def _check_image(filename: str, data: bytes) -> UploadedImage:
    detected = sniff_image(data)
    if detected is None:
        raise _bad_request(f"{filename} 不是有效的 PNG、JPEG、WEBP 或 GIF 图片")
    try:
        width, height = image_size(data)
    except Image.DecompressionBombError as exc:
        raise _too_large(f"{filename} 像素过大，请缩小后再上传") from exc
    except Exception as exc:  # noqa: BLE001 - Pillow 对坏图会抛多种异常
        raise _unprocessable(f"{filename} 无法读取，请确认图片没有损坏") from exc
    content_type, extension = detected
    return UploadedImage(filename=filename[:200], content_type=content_type, extension=extension, data=data, width=width, height=height)


def _check_document(filename: str, extension: str, data: bytes) -> None:
    if extension == ".pdf":
        _check_pdf(filename, data)
    elif extension in _ZIP_KINDS:
        _check_office_zip(filename, extension, data)
    elif extension == ".xls":
        if not data.startswith(_OLE_MAGIC):
            raise _bad_request(f"{filename} 不是有效的 xls 文件，请另存为 xlsx 后再上传")
    elif b"\x00" in data[:8192]:
        raise _bad_request(f"{filename} 不是文本文件，请确认扩展名正确")


def _check_pdf(filename: str, data: bytes) -> None:
    if b"%PDF" not in data[:1024]:
        raise _bad_request(f"{filename} 不是有效的 PDF 文件")
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise _unprocessable(f"{filename} 已加密，请去掉密码后再上传") from exc
        raise _unprocessable(f"{filename} 无法打开，请确认文件没有损坏") from exc
    try:
        pages = len(document)
    finally:
        document.close()
    if pages == 0:
        raise _unprocessable(f"{filename} 没有任何页面")
    if pages > PDF_MAX_PAGES:
        raise _unprocessable(f"{filename} 共 {pages} 页，当前最多支持 {PDF_MAX_PAGES} 页，请拆分或节选后再上传")


def _check_office_zip(filename: str, extension: str, data: bytes) -> None:
    if data.startswith(_OLE_MAGIC):
        # 加了打开密码的 Office 文件和 97-2003 旧格式都是 OLE 容器。
        raise _unprocessable(f"{filename} 已加密或是旧版格式，请去掉密码并另存为 {extension.lstrip('.')} 后再上传")
    if not data.startswith(_ZIP_MAGIC):
        raise _bad_request(f"{filename} 不是有效的 {extension.lstrip('.')} 文件")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise _unprocessable(f"{filename} 无法打开，请确认文件没有损坏") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > ZIP_MAX_ENTRIES or sum(entry.file_size for entry in entries) > ZIP_MAX_UNCOMPRESSED_BYTES:
            raise _too_large(f"{filename} 解压后内容过大，请精简后再上传")
        names = {entry.filename for entry in entries}
        if _ZIP_KINDS[extension] not in names:
            raise _bad_request(f"{filename} 不是有效的 {extension.lstrip('.')} 文件")
        if any(entry.flag_bits & 0x1 for entry in entries):
            raise _unprocessable(f"{filename} 已加密，请去掉密码后再上传")
        if extension == ".pptx":
            slides = sum(1 for name in names if _SLIDE_ENTRY.match(name))
            if slides > PPTX_MAX_SLIDES:
                raise _unprocessable(f"{filename} 共 {slides} 页，当前最多支持 {PPTX_MAX_SLIDES} 页，请精简后再上传")
    if extension == ".docx":
        _check_docx_blocks(filename, data)


def _check_docx_blocks(filename: str, data: bytes) -> None:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise _unprocessable(f"{filename} 无法打开，请确认文件没有损坏") from exc
    blocks = sum(1 for child in document.element.body if child.tag.rsplit("}", 1)[-1] in {"p", "tbl"})
    if blocks > DOCX_MAX_BLOCKS:
        raise _unprocessable(f"{filename} 段落和表格过多（{blocks} 段），当前最多支持 {DOCX_MAX_BLOCKS} 段，请精简后再上传")
