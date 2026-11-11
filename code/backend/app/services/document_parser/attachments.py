"""写码阶段的图片双通道。

- 通道二（页面里用）：在消息里列出 /assets/{id} 地址。场景槽位图和用户上传图已在创建任务时写进需求；
  这里补的是文档配图。整页快照永远不列地址。
- 通道一（让模型看到）：把图片缩小后转成 data URL 挂在本轮消息上。只在本轮新上传了图片，或本轮提取
  结果用得到某些资料图片时才附；续写轮次不再重复附历史图片。最多 6 张。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.entities import GenerationBatch, PageAsset
from app.services.document_parser.context import LineageMaterial
from app.services.document_parser.extractor_agent import ExtractionResult
from app.services.document_parser.images import to_data_url
from app.services.llm.types import LlmImagePart, LlmUsage
from app.services.storage.factory import create_storage_provider

MAX_ATTACHED_IMAGES = 6
USER_IMAGE_KINDS = ("scene_slot", "user_upload")


@dataclass
class _Visual:
    storage_key: str
    label: str
    kind: str  # user_image / snapshot / doc_image
    priority: int
    area: int = 0


@dataclass
class DocumentContext:
    """本轮交给写码模型的资料：提取文本、可引用的文档配图、要附带的图片。"""

    # none：会话里没有资料；extracted：提取到相关内容；empty：本轮与资料无关；visual：纯视觉资料直通
    mode: str = "none"
    text: str = ""
    listed_assets: list[PageAsset] = field(default_factory=list)
    doc_visuals: list[_Visual] = field(default_factory=list)
    usage: LlmUsage | None = None
    prompt_log: str = ""
    image_note: str = ""
    attached_labels: list[str] = field(default_factory=list)

    def prompt_block(self) -> str:
        parts: list[str] = []
        if self.text:
            intro = (
                "以下是用户上传的资料，以图片为主，文字很少。请结合随消息附上的图片理解内容。"
                if self.mode == "visual"
                else "以下是从用户上传资料中提取的、与本轮需求相关的内容。请据此组织页面的内容、数据和结构，不要编造资料里没有的信息。"
            )
            parts.append(f"{intro}\n\n# 资料提取内容\n{self.text}")
        if self.listed_assets:
            base = get_settings().public_base_url.rstrip("/")
            lines = [
                "资料中的配图（平台托管地址，不算外链，可按需用 <img> 引用；不需要的可以不用。锚点里的 img:id 对应下面同名条目）："
            ]
            for asset in self.listed_assets:
                size = f"｜{asset.width}×{asset.height}" if asset.width and asset.height else ""
                lines.append(f"- img:{asset.id} → {base}/assets/{asset.id}｜{asset.source_label or asset.filename or ''}{size}")
            parts.append("\n".join(lines))
        if self.image_note:
            parts.append(self.image_note)
        return "\n\n".join(parts)


def build_document_context(
    material: LineageMaterial,
    extraction: ExtractionResult | None,
    *,
    visual_direct: bool,
) -> DocumentContext:
    """按提取结果决定本轮列哪些配图地址、附哪些资料图片。"""
    if not material.has_documents:
        return DocumentContext()
    if visual_direct:
        assets, snaps = material.current_visuals()
        context = DocumentContext(mode="visual", text=material.text, listed_assets=_current_assets(material))
        context.doc_visuals = [_snapshot_visual(snap) for snap in snaps] + [_asset_visual(asset) for asset in assets]
        return context
    if extraction is None or extraction.empty:
        if extraction is None:
            return DocumentContext(mode="empty")
        return DocumentContext(mode="empty", usage=extraction.usage, prompt_log=extraction.prompt_log)

    refs = set(extraction.image_refs)
    by_id = {str(asset.id): asset for asset in material.doc_assets}
    referenced = [by_id[ref] for ref in extraction.image_refs if ref in by_id]
    listed = _current_assets(material)
    listed += [asset for asset in referenced if asset not in listed]
    snaps = [snap for snap in material.snapshots if str(snap.get("snap_id", "")).lower() in refs]
    return DocumentContext(
        mode="extracted",
        text=extraction.text,
        listed_assets=listed,
        doc_visuals=[_snapshot_visual(snap) for snap in snaps] + [_asset_visual(asset) for asset in referenced],
        usage=extraction.usage,
        prompt_log=extraction.prompt_log,
    )


def _current_assets(material: LineageMaterial) -> list[PageAsset]:
    """本轮新上传的文档：它的配图都列出来供首轮选用。更早轮次的配图只在被提取结果点名时才列。"""
    if not material.current_batch_has_documents:
        return []
    return [asset for asset in material.doc_assets if asset.batch_id == material.current_batch_id]


def _snapshot_visual(snap: dict[str, Any]) -> _Visual:
    label = f"资料整页快照：{snap.get('filename', '')} 第 {snap.get('page_no', '?')} 页（snap:{snap.get('snap_id')}，只用于阅读内容，不要放进页面）"
    return _Visual(storage_key=snap["storage_key"], label=label, kind="snapshot", priority=2)


def _asset_visual(asset: PageAsset) -> _Visual:
    label = f"资料配图 img:{asset.id}（{asset.source_label or asset.filename or ''}）"
    return _Visual(
        storage_key=asset.storage_key, label=label, kind="doc_image", priority=3, area=(asset.width or 0) * (asset.height or 0)
    )


async def load_attachments(
    session: AsyncSession,
    batch: GenerationBatch | None,
    context: DocumentContext,
    *,
    supports_vision: bool,
) -> tuple[tuple[LlmImagePart, ...], bool]:
    """取本轮要附带的图片并转成 data URL。返回 (图片, 是否因模型不支持视觉而跳过)。"""
    visuals: list[_Visual] = []
    if batch is not None:
        result = await session.execute(
            select(PageAsset)
            .where(PageAsset.batch_id == batch.id, PageAsset.source_kind.in_(USER_IMAGE_KINDS))
            .order_by(PageAsset.created_at.asc())
        )
        for asset in result.scalars():
            kind_label = "场景图片" if asset.source_kind == "scene_slot" else "上传的图片"
            visuals.append(
                _Visual(
                    storage_key=asset.storage_key,
                    label=f"{kind_label}「{asset.filename or asset.role}」（已托管，地址见需求）",
                    kind="user_image",
                    priority=0 if asset.source_kind == "scene_slot" else 1,
                )
            )
    visuals += context.doc_visuals
    if not visuals:
        return (), False
    if not supports_vision:
        return (), True

    visuals.sort(key=lambda item: (item.priority, -item.area))
    storage = create_storage_provider()
    images: list[LlmImagePart] = []
    labels: list[str] = []
    for visual in visuals:
        if len(images) >= MAX_ATTACHED_IMAGES:
            break
        try:
            data = await storage.get_bytes(visual.storage_key)
        except Exception:  # noqa: BLE001 - 单张取不到不影响生成
            continue
        data_url = await asyncio.to_thread(to_data_url, data)
        if data_url is None:
            continue
        images.append(LlmImagePart(data_url=data_url, label=visual.label))
        labels.append(visual.label)
    if labels:
        context.attached_labels = labels
        context.image_note = "随本条消息附上 {count} 张图片，供你查看内容和风格：\n{items}".format(
            count=len(labels), items="\n".join(f"{index}. {label}" for index, label in enumerate(labels, start=1))
        )
    return tuple(images), False
