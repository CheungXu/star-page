"""按扩展名分发到各格式解析器。同步、CPU 密集，调用方放进线程里跑。"""

from __future__ import annotations

import hashlib

from app.services.document_parser.docx_parser import parse_docx
from app.services.document_parser.excel_parser import parse_excel
from app.services.document_parser.html_parser import parse_html, parse_plain_text
from app.services.document_parser.pdf_parser import parse_pdf
from app.services.document_parser.pptx_parser import parse_pptx
from app.services.document_parser.types import ParsedDocument


def parse_document(filename: str, extension: str, data: bytes) -> ParsedDocument:
    digest = hashlib.sha256(data).hexdigest()
    extension = extension.lower()
    if extension == ".pdf":
        return parse_pdf(filename, data, digest)
    if extension == ".pptx":
        return parse_pptx(filename, data, digest)
    if extension == ".docx":
        return parse_docx(filename, data, digest)
    if extension in {".xlsx", ".xls"}:
        return parse_excel(filename, data, digest, extension)
    if extension in {".html", ".htm"}:
        return parse_html(filename, data, digest, extension)
    return parse_plain_text(filename, data, digest, extension)
