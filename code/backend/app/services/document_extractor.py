"""兼容旧导入路径。解析与提取已移到 app.services.document_parser。"""

from __future__ import annotations

from app.services.document_parser import (
    SUPPORTED_EXTENSIONS,
    SUPPORTED_FILE_TYPES_LABEL,
    DocumentExtractionError,
    DocumentParseError,
    DocumentStageError,
    parse_document,
    precheck_uploads,
)
from app.services.document_parser.limits import MAX_FILE_COUNT, MAX_FILE_SIZE_BYTES, MAX_TOTAL_FILE_SIZE_BYTES

__all__ = [
    "MAX_FILE_COUNT",
    "MAX_FILE_SIZE_BYTES",
    "MAX_TOTAL_FILE_SIZE_BYTES",
    "SUPPORTED_EXTENSIONS",
    "SUPPORTED_FILE_TYPES_LABEL",
    "DocumentExtractionError",
    "DocumentParseError",
    "DocumentStageError",
    "parse_document",
    "precheck_uploads",
]
