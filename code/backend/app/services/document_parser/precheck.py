"""创建请求里的同步预检：坏文件、超限文件直接 4xx，不建批次、不扣免费次数。

只做便宜的检查（文件头、能否打开、是否加密、页数、zip 解压规模、DOCX 流式段落计数），
直接操作上传临时文件流，不在 web 进程里整份读入字节或构建完整 DOM；完整解析在后台做。
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO, Union

from fastapi import HTTPException, status
from PIL import Image
from starlette.datastructures import UploadFile

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

UploadEntry = Union[UploadFile, tuple[str, Union[bytes, BinaryIO]]]


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _too_large(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=detail)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _check_size_limits(filename: str, extension: str, size: int, total: int) -> int:
    if size <= 0:
        raise _unprocessable(f"{filename} 是空文件，请重新选择")
    if extension in IMAGE_EXTENSIONS and size > MAX_UPLOAD_IMAGE_BYTES:
        raise _too_large(f"{filename} 超过 10MB，请压缩后再上传")
    if size > MAX_FILE_SIZE_BYTES:
        raise _too_large(f"{filename} 超过 50MB，请压缩或拆分后再上传")
    total += size
    if total > MAX_TOTAL_FILE_SIZE_BYTES:
        raise _too_large("本次上传文件总大小超过 50MB，请减少文件大小")
    return total


def _stream_size(stream: BinaryIO) -> int:
    stream.seek(0, io.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


def precheck_uploads(files: Sequence[UploadEntry]) -> tuple[list[StagedDocument], list[UploadedImage]]:
    """支持直接传入 Starlette UploadFile 或 (文件名, bytes | BinaryIO)。CPU 较重，调用方放进线程里跑。"""
    entries: list[tuple[str, bytes | None, BinaryIO, int | None]] = []
    for item in files:
        if isinstance(item, UploadFile) or (hasattr(item, "file") and hasattr(item, "filename")):
            raw_name = getattr(item, "filename", None) or ""
            if not raw_name:
                continue
            known_size = getattr(item, "size", None)
            entries.append((Path(raw_name).name, None, item.file, int(known_size) if known_size is not None else None))
        else:
            raw_name, payload = item
            if not raw_name:
                continue
            name = Path(raw_name).name
            if isinstance(payload, (bytes, bytearray, memoryview)):
                raw_bytes = bytes(payload)
                entries.append((name, raw_bytes, io.BytesIO(raw_bytes), len(raw_bytes)))
            else:
                entries.append((name, None, payload, None))

    if len(entries) > MAX_FILE_COUNT:
        raise _too_large(f"当前一次最多上传 {MAX_FILE_COUNT} 个文件")

    documents: list[StagedDocument] = []
    images: list[UploadedImage] = []
    total = 0
    for filename, raw_bytes, stream, known_size in entries:
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise _bad_request(f"{filename} 的格式暂不支持，请上传 {SUPPORTED_FILE_TYPES_LABEL} 文件")

        # 若 UploadFile.size 已知，读取任何内容之前先拦截空文件与超限文件。
        if known_size is not None:
            total = _check_size_limits(filename, extension, known_size, total)
            size = known_size
        else:
            size = _stream_size(stream)
            total = _check_size_limits(filename, extension, size, total)

        if extension in IMAGE_EXTENSIONS:
            if raw_bytes is None:
                stream.seek(0)
                raw_bytes = stream.read(MAX_UPLOAD_IMAGE_BYTES + 1)
                if len(raw_bytes) > MAX_UPLOAD_IMAGE_BYTES:
                    raise _too_large(f"{filename} 超过 10MB，请压缩后再上传")
            images.append(_check_image(filename, raw_bytes))
        elif extension in DOCUMENT_EXTENSIONS:
            _check_document(filename, extension, stream)
            stream.seek(0)
            documents.append(
                StagedDocument(
                    filename=filename,
                    extension=extension,
                    data=raw_bytes,
                    file=None if raw_bytes is not None else stream,
                    byte_size=size,
                )
            )
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


def _check_document(filename: str, extension: str, stream: BinaryIO) -> None:
    stream.seek(0)
    if extension == ".pdf":
        _check_pdf(filename, stream)
    elif extension in _ZIP_KINDS:
        _check_office_zip(filename, extension, stream)
    elif extension == ".xls":
        magic = stream.read(len(_OLE_MAGIC))
        if magic != _OLE_MAGIC:
            raise _bad_request(f"{filename} 不是有效的 xls 文件，请另存为 xlsx 后再上传")
        stream.seek(0)
        _check_workbook_opens(filename, stream)
    else:
        sample = stream.read(8192)
        if b"\x00" in sample:
            raise _bad_request(f"{filename} 不是文本文件，请确认扩展名正确")


def _check_pdf(filename: str, stream: BinaryIO) -> None:
    head = stream.read(1024)
    if b"%PDF" not in head:
        raise _bad_request(f"{filename} 不是有效的 PDF 文件")
    stream.seek(0)
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(stream)
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


def _check_office_zip(filename: str, extension: str, stream: BinaryIO) -> None:
    magic = stream.read(len(_OLE_MAGIC))
    if magic == _OLE_MAGIC:
        # 加了打开密码的 Office 文件和 97-2003 旧格式都是 OLE 容器。
        raise _unprocessable(f"{filename} 已加密或是旧版格式，请去掉密码并另存为 {extension.lstrip('.')} 后再上传")
    if not magic.startswith(_ZIP_MAGIC):
        raise _bad_request(f"{filename} 不是有效的 {extension.lstrip('.')} 文件")
    stream.seek(0)
    try:
        archive = zipfile.ZipFile(stream)
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
        elif extension == ".docx":
            _check_docx_blocks(filename, archive)
    if extension == ".xlsx":
        stream.seek(0)
        _check_workbook_opens(filename, stream)


def _check_workbook_opens(filename: str, stream: BinaryIO) -> None:
    """只读工作表目录，损坏或加密的表格在这里就拦下。"""
    from python_calamine import CalamineWorkbook

    try:
        workbook = CalamineWorkbook.from_filelike(stream)
        names = workbook.sheet_names
    except Exception as exc:  # noqa: BLE001 - calamine 对坏文件、加密文件抛自己的异常
        raise _unprocessable(f"{filename} 无法打开，请确认文件未加密且没有损坏") from exc
    if not names:
        raise _unprocessable(f"{filename} 没有任何工作表")


def _check_docx_blocks(filename: str, archive: zipfile.ZipFile) -> None:
    """用 iterparse 流式统计 w:body 下的直接段落和表格数，边读边清节点，不构建整棵 DOM。"""
    blocks = 0
    in_body = False
    body_depth = 0
    body_elem: ET.Element | None = None
    try:
        with archive.open("word/document.xml") as xml_file:
            for event, elem in ET.iterparse(xml_file, events=("start", "end")):
                local = elem.tag.rsplit("}", 1)[-1]
                if event == "start":
                    if not in_body:
                        if local == "body":
                            in_body = True
                            body_depth = 0
                            body_elem = elem
                    else:
                        body_depth += 1
                        if body_depth == 1 and local in {"p", "tbl"}:
                            blocks += 1
                            if blocks > DOCX_MAX_BLOCKS:
                                raise _unprocessable(
                                    f"{filename} 段落和表格过多（超过 {DOCX_MAX_BLOCKS} 段），当前最多支持 {DOCX_MAX_BLOCKS} 段，请精简后再上传"
                                )
                else:
                    if in_body:
                        if body_depth == 0 and local == "body":
                            in_body = False
                            elem.clear()
                            body_elem = None
                        else:
                            is_direct_child = body_depth == 1
                            body_depth -= 1
                            elem.clear()
                            if is_direct_child and body_elem is not None:
                                body_elem.clear()
                    else:
                        elem.clear()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 损坏的 XML 在这里拦下
        raise _unprocessable(f"{filename} 无法打开，请确认文件没有损坏") from exc
