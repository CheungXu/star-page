from __future__ import annotations


class DocumentStageError(Exception):
    """资料阶段（解析或提取）失败。user_message 给用户看，detail 留给排查。"""

    # 写进任务 error_message 的前缀，整批是否都败在资料阶段靠它判断。
    ERROR_PREFIX = "document_stage: "

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(f"{message} | {detail}" if detail else message)
        self.user_message = message
        self.detail = detail


class DocumentParseError(DocumentStageError):
    """后台解析失败：文件损坏、超时或超出上限。"""


class DocumentExtractionError(DocumentStageError):
    """同模型信息提取失败或超时。"""
