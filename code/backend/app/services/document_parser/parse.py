"""按扩展名分发到各格式解析器。同步、CPU 密集，由独立解析子进程（或线程）调用。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.services.document_parser.docx_parser import parse_docx
from app.services.document_parser.excel_parser import parse_excel
from app.services.document_parser.html_parser import parse_html, parse_plain_text
from app.services.document_parser.pdf_parser import parse_pdf
from app.services.document_parser.pptx_parser import parse_pptx
from app.services.document_parser.types import ParsedDocument


def _sha256(source: bytes | Path) -> str:
    if isinstance(source, Path):
        with source.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    return hashlib.sha256(source).hexdigest()


def parse_document(filename: str, extension: str, data: bytes | Path) -> ParsedDocument:
    digest = _sha256(data)
    extension = extension.lower()
    if extension == ".pdf":
        return parse_pdf(filename, data, digest)
    if extension == ".pptx":
        return parse_pptx(filename, data, digest)
    if extension == ".docx":
        return parse_docx(filename, data, digest)
    if extension in {".xlsx", ".xls"}:
        return parse_excel(filename, data, digest, extension)
    raw = data.read_bytes() if isinstance(data, Path) else data
    if extension in {".html", ".htm"}:
        return parse_html(filename, raw, digest, extension)
    return parse_plain_text(filename, raw, digest, extension)
