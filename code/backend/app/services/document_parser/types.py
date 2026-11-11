from __future__ import annotations

from dataclasses import dataclass, field

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
    """创建请求里通过预检、等待后台解析的文档。"""

    filename: str
    extension: str
    data: bytes


@dataclass(frozen=True)
class UploadedImage:
    """通用上传口收到的图片，通过预检后直接入库。"""

    filename: str
    content_type: str
    extension: str
    data: bytes
    width: int
    height: int
