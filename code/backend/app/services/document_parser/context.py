"""沿会话线收集资料：这一轮及之前所有轮次的文档原文、文档配图和整页快照。

用 view_batches 取轮次（与会话详情接口同一口径），分叉会话自然继承分叉点之前的资料。
同一个文件在多轮里重复上传时按原始字节哈希去重，只保留最早一份。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Conversation, GenerationBatch, PageAsset
from app.services.conversation_tree import take_until, view_batches
from app.services.document_parser.text_utils import effective_char_count

MAX_LINEAGE_CHARS = 120_000
# 资料除锚点外不到这么多字、又有快照或核心大图，视为「纯视觉资料」。
VISUAL_ONLY_CHARS = 100


@dataclass
class LineageMaterial:
    text: str = ""
    file_names: list[str] = field(default_factory=list)
    # 本轮新上传的文档带来的资料
    current_batch_has_documents: bool = False
    doc_assets: list[PageAsset] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    current_batch_id: uuid.UUID | None = None
    truncated: bool = False

    @property
    def has_documents(self) -> bool:
        return bool(self.text.strip())

    @property
    def visual_only(self) -> bool:
        has_visual = bool(self.snapshots) or any(_is_core(asset) for asset in self.doc_assets)
        return has_visual and effective_char_count(self.text) < VISUAL_ONLY_CHARS

    def current_visuals(self) -> tuple[list[PageAsset], list[dict[str, Any]]]:
        """本轮新上传文档里的核心大图和快照，纯视觉资料直通时交给写码模型。"""
        assets = [asset for asset in self.doc_assets if asset.batch_id == self.current_batch_id and _is_core(asset)]
        snaps = [snap for snap in self.snapshots if snap.get("batch_id") == str(self.current_batch_id)]
        return assets, snaps


def _is_core(asset: PageAsset) -> bool:
    return "核心内容大图" in (asset.source_label or "")


async def collect_lineage(session: AsyncSession, batch: GenerationBatch) -> LineageMaterial:
    conversation = await session.get(Conversation, batch.conversation_id)
    batches = take_until(await view_batches(session, conversation), batch.id) if conversation else [batch]
    if not batches or batches[-1].id != batch.id:
        batches = [*batches, batch]

    material = LineageMaterial(current_batch_id=batch.id)
    seen: set[str] = set()
    pieces: list[tuple[uuid.UUID, str]] = []
    for item in batches:
        for name in item.input_file_names or []:
            if name not in material.file_names:
                material.file_names.append(name)
        for document in _documents_of(item):
            if document["sha256"] in seen:
                continue
            seen.add(document["sha256"])
            pieces.append((item.id, document["text"]))
            if item.id == batch.id:
                material.current_batch_has_documents = True
        for snap in item.visual_snapshots or []:
            material.snapshots.append({**snap, "batch_id": str(item.id)})

    material.text, material.truncated = _fit(pieces, batch.id)

    batch_ids = [item.id for item in batches]
    result = await session.execute(
        select(PageAsset)
        .where(PageAsset.batch_id.in_(batch_ids), PageAsset.source_kind == "doc_embedded")
        .order_by(PageAsset.created_at.asc(), PageAsset.sort_order.asc())
    )
    material.doc_assets = list(result.scalars().all())
    return material


def _documents_of(batch: GenerationBatch) -> list[dict[str, str]]:
    """按 parsed_documents 的起止位置把合并原文切回单个文件；老数据没有位置信息，整段当一个文件。"""
    text = batch.extracted_file_text or ""
    if not text.strip():
        return []
    meta = batch.parsed_documents or []
    if not meta:
        return [{"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "text": text}]
    documents = []
    for item in meta:
        start, end = int(item.get("start", 0)), int(item.get("end", len(text)))
        documents.append({"sha256": str(item.get("sha256") or ""), "text": text[start:end]})
    return documents


def _fit(pieces: list[tuple[uuid.UUID, str]], current_batch_id: uuid.UUID) -> tuple[str, bool]:
    """总长超限时优先保留本轮的资料，其余按从新到旧保留，最早的截断。"""
    total = sum(len(text) for _bid, text in pieces) + 2 * max(len(pieces) - 1, 0)
    if total <= MAX_LINEAGE_CHARS:
        return "\n\n".join(text for _bid, text in pieces), False
    budget = MAX_LINEAGE_CHARS
    kept: dict[int, str] = {}
    order = [index for index, (bid, _t) in enumerate(pieces) if bid == current_batch_id]
    order += [index for index in reversed(range(len(pieces))) if pieces[index][0] != current_batch_id]
    for index in order:
        if budget <= 200:
            break
        text = pieces[index][1]
        if len(text) > budget:
            text = text[:budget] + "\n（资料过长，以下内容已截断）"
        kept[index] = text
        budget -= len(text) + 2
    return "\n\n".join(kept[index] for index in sorted(kept)), True
