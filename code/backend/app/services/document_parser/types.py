from __future__ import annotations

from dataclasses import dataclass, field
from typing import BinaryIO

# 解析器在正文里先留占位，入库拿到资源 ID 后再换成锚点文本。
IMAGE_PLACEHOLDER = "⟦IMG:{index}⟧"
SNAPSHOT_PLACEHOLDER = "⟦SNAP:{index}⟧"


@dataclass
class ParsedImage:
    """文档里取出的一张配图，已归一化成网页可用的格式。"""

    data: bytes
    content_type: str
    extension: str
    width: int
    height: int
    sha256: str
    label: str
    area_ratio: float = 0.0
    # 少字页上占大半版面的图：这一页的主要信息在图里。
    core: bool = False


@dataclass
class ParsedSnapshot:
    """少字大图页、扫描页的整页渲染图，只给模型看，不进公开资源。"""

    data: bytes
    width: int
    height: int
    page_no: int
    reason: str


@dataclass
class ParsedDocument:
    filename: str
    extension: str
    # 原始文件字节的哈希，多轮重复上传同一文件时据此去重。
    content_sha256: str
    text: str
    images: list[ParsedImage] = field(default_factory=list)
    snapshots: list[ParsedSnapshot] = field(default_factory=list)


@dataclass(frozen=True)
class StagedDocument:
    """创建请求里通过预检、等待后台解析的文档。

    走 multipart 上传时直接持有 Starlette 的临时文件对象 file，不在进程内存里拷整份字节；
    直接传 bytes 的调用方（如单测）仍可填 data。
    """

    filename: str
    extension: str
    data: bytes | None = None
    file: BinaryIO | None = None
    byte_size: int = 0

    def __post_init__(self) -> None:
        if self.byte_size == 0 and self.data is not None:
            object.__setattr__(self, "byte_size", len(self.data))


@dataclass(frozen=True)
class UploadedImage:
    """通用上传口收到的图片，通过预检后直接入库。"""

    filename: str
    content_type: str
    extension: str
    data: bytes
    width: int
    height: int
