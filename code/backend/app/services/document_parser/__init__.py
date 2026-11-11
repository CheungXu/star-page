"""上传资料解析：预检、分格式解析、批次后台解析、跨轮资料收集与同模型信息提取。说明见本目录 README。"""

from app.services.document_parser.errors import DocumentExtractionError, DocumentParseError, DocumentStageError
from app.services.document_parser.limits import SUPPORTED_EXTENSIONS, SUPPORTED_FILE_TYPES_LABEL
from app.services.document_parser.parse import parse_document
from app.services.document_parser.precheck import precheck_uploads

__all__ = [
    "DocumentExtractionError",
    "DocumentParseError",
    "DocumentStageError",
    "SUPPORTED_EXTENSIONS",
    "SUPPORTED_FILE_TYPES_LABEL",
    "parse_document",
    "precheck_uploads",
]
