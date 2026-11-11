"""批次级后台解析：创建请求只暂存原件，第一个开跑的模型拉起解析，同批其他模型等同一个结果。

- 解析是独立的 asyncio.Task，不属于任何一个模型的协程：停掉某个模型不会打断解析，
  只有同批所有等待者都被取消时才取消解析。
- 单进程假设与 generation_runner 一致：任务登记表在内存里，状态用 parse_status 原子更新兜底。
- 原件只在解析期间存在：成功、失败、取消都会删掉。整页快照存私有键，不进 page_assets。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Conversation, GenerationBatch, GenerationTask, PageAsset, User
from app.services.document_parser.errors import DocumentParseError
from app.services.document_parser.limits import BATCH_MAX_SNAPSHOTS, PARSE_TIMEOUT_SECONDS
from app.services.document_parser.parse import parse_document
from app.services.document_parser.types import IMAGE_PLACEHOLDER, SNAPSHOT_PLACEHOLDER, ParsedDocument, StagedDocument
from app.services.storage.factory import create_storage_provider

STAGING_PREFIX = "batch-uploads"
SNAPSHOT_PREFIX = "batch-private"
CANCELLED_PARSE_MESSAGE = "已停止生成"


def staging_key(staging_id: uuid.UUID, index: int, extension: str) -> str:
    # 文件名不进存储键，避免路径里出现用户输入。
    return f"{STAGING_PREFIX}/{staging_id}/{index}{extension}"


def snapshot_key(batch_id: uuid.UUID, snap_id: str) -> str:
    return f"{SNAPSHOT_PREFIX}/{batch_id}/snapshots/{snap_id}.png"


async def stage_documents(documents: list[StagedDocument]) -> list[dict[str, Any]]:
    """把通过预检的文档原件写进私有存储，返回 staged_uploads 元数据。中途失败会删掉已写入的。"""
    if not documents:
        return []
    storage = create_storage_provider()
    staging_id = uuid.uuid4()
    staged: list[dict[str, Any]] = []
    try:
        for index, document in enumerate(documents):
            key = staging_key(staging_id, index, document.extension)
            await storage.put_bytes(key, document.data, "application/octet-stream")
            staged.append(
                {"storage_key": key, "filename": document.filename, "extension": document.extension, "byte_size": len(document.data)}
            )
    except Exception:
        await discard_staged(staged)
        raise
    return staged


async def discard_staged(staged: list[dict[str, Any]]) -> None:
    await _delete_keys([item.get("storage_key") for item in staged])


async def discard_snapshots(snapshots: list[dict[str, Any]]) -> None:
    await _delete_keys([item.get("storage_key") for item in snapshots])


async def _delete_keys(keys: list[str | None]) -> None:
    keys = [key for key in keys if key]
    if not keys:
        return
    storage = create_storage_provider()
    for key in keys:
        try:
            await storage.delete(key)
        except Exception:  # noqa: BLE001 - 清理失败不影响主流程，原件已不再被引用
            continue


def anchor_for_image(asset_id: uuid.UUID, label: str, width: int, height: int, core: bool) -> str:
    kind = "核心内容大图" if core else "配图"
    tail = "｜这一页的主要信息在图里" if core else ""
    return f"[{kind} img:{asset_id}｜{label}｜{width}×{height}{tail}]"


def anchor_for_snapshot(snap_id: str, filename: str, page_no: int, reason: str) -> str:
    return f"[整页快照 snap:{snap_id}｜{filename} 第 {page_no} 页｜{reason}]"


# ---------- 批次共享解析 ----------


@dataclass
class _ParseJob:
    task: asyncio.Task[None]
    waiters: int = 0


_jobs: dict[uuid.UUID, _ParseJob] = {}
_session_factory: Callable[[], AsyncSession] | None = None


def configure_session_factory(factory: Callable[[], AsyncSession] | None) -> None:
    """测试用：替换解析任务自己的数据库会话工厂。"""
    global _session_factory
    _session_factory = factory


def _new_session() -> AsyncSession:
    if _session_factory is not None:
        return _session_factory()
    from app.core.database import AsyncSessionLocal

    return AsyncSessionLocal()


def batch_needs_parse(batch: GenerationBatch | None) -> bool:
    return batch is not None and batch.parse_status in {"pending", "running"}


async def wait_for_batch_parse(batch_id: uuid.UUID) -> None:
    """等本批解析完成。失败抛 DocumentParseError；同批所有等待者都被取消时才取消解析。"""
    job = _jobs.get(batch_id)
    if job is None or job.task.done():
        job = _ParseJob(task=asyncio.create_task(_run_parse(batch_id), name=f"parse-{batch_id}"))
        _jobs[batch_id] = job
    job.waiters += 1
    cancelled = False
    try:
        await asyncio.shield(job.task)
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        job.waiters -= 1
        if job.waiters <= 0:
            if cancelled and not job.task.done():
                job.task.cancel()
            if _jobs.get(batch_id) is job:
                _jobs.pop(batch_id, None)


async def _run_parse(batch_id: uuid.UUID) -> None:
    async with _new_session() as session:
        batch = await session.get(GenerationBatch, batch_id)
        if batch is None or batch.parse_status in {"none", "succeeded"}:
            return
        if batch.parse_status == "failed":
            raise DocumentParseError(batch.parse_error or "文件解析失败，请重新上传")
        # pending 正常认领；running 只可能是上个进程留下的（扫尾会重置），本进程接手。
        await session.execute(
            update(GenerationBatch).where(GenerationBatch.id == batch_id).values(parse_status="running")
        )
        await session.commit()
        await session.refresh(batch)

        stored_assets: list[PageAsset] = []
        snapshots: list[dict[str, Any]] = []
        try:
            parsed = await _parse_staged(batch.staged_uploads or [])
            owner_id = await _batch_owner_id(session, batch)
            text, documents_meta = await _store_outputs(session, batch, owner_id, parsed, stored_assets, snapshots)
            staged = list(batch.staged_uploads or [])
            batch.extracted_file_text = text
            batch.parsed_documents = documents_meta
            batch.visual_snapshots = snapshots
            batch.staged_uploads = []
            batch.parse_status = "succeeded"
            batch.parse_error = None
            await session.execute(
                update(GenerationTask).where(GenerationTask.batch_id == batch_id).values(extracted_file_text=text)
            )
            await session.commit()
            await discard_staged(staged)
        except asyncio.CancelledError:
            await asyncio.shield(_mark_failed(batch_id, CANCELLED_PARSE_MESSAGE, stored_assets, snapshots, refund=False))
            raise
        except DocumentParseError as exc:
            await session.rollback()
            await _mark_failed(batch_id, exc.user_message, stored_assets, snapshots, refund=True)
            raise
        except Exception as exc:  # noqa: BLE001 - 解析器抛什么都要落成用户可读的失败
            await session.rollback()
            message = "文件解析失败，请确认文件未加密且内容可读取"
            await _mark_failed(batch_id, message, stored_assets, snapshots, refund=True)
            raise DocumentParseError(message, detail=f"{type(exc).__name__}: {exc}") from exc


async def _parse_staged(staged: list[dict[str, Any]]) -> list[ParsedDocument]:
    storage = create_storage_provider()
    parsed: list[ParsedDocument] = []
    for item in staged:
        filename = item.get("filename") or "未命名文件"
        data = await storage.get_bytes(item["storage_key"])
        try:
            # 线程里的解析超时后无法强行中止，只能不再等它；上限保证了单个文件的工作量有界。
            document = await asyncio.wait_for(
                asyncio.to_thread(parse_document, filename, item.get("extension") or "", data),
                timeout=PARSE_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise DocumentParseError(f"{filename} 解析超时，请精简后再上传", detail=f"超过 {PARSE_TIMEOUT_SECONDS}s") from exc
        except Exception as exc:  # noqa: BLE001
            hint = "；扫描件请确认每页都能正常打开" if item.get("extension") == ".pdf" else ""
            raise DocumentParseError(
                f"{filename} 解析失败，请确认文件未加密且内容可读取{hint}", detail=f"{type(exc).__name__}: {exc}"
            ) from exc
        parsed.append(document)
    return parsed


async def _batch_owner_id(session: AsyncSession, batch: GenerationBatch) -> uuid.UUID:
    conversation = await session.get(Conversation, batch.conversation_id)
    if conversation is None:
        raise DocumentParseError("会话不存在")
    return conversation.owner_user_id


async def _store_outputs(
    session: AsyncSession,
    batch: GenerationBatch,
    owner_id: uuid.UUID,
    parsed: list[ParsedDocument],
    stored_assets: list[PageAsset],
    snapshots: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """配图写 page_assets，快照写私有键，占位换成锚点，拼出带锚点的合并原文。"""
    storage = create_storage_provider()
    seen_hashes: set[str] = set()
    blocks: list[str] = []
    documents_meta: list[dict[str, Any]] = []
    cursor = 0
    sort_order = 0
    for doc_index, document in enumerate(parsed, start=1):
        text = document.text
        for index, image in enumerate(document.images):
            placeholder = IMAGE_PLACEHOLDER.format(index=index)
            if image.sha256 in seen_hashes:
                text = text.replace(placeholder, "")
                continue
            seen_hashes.add(image.sha256)
            asset_id = uuid.uuid4()
            key = f"page-assets/{asset_id}{image.extension}"
            await storage.put_bytes(key, image.data, image.content_type)
            asset = PageAsset(
                id=asset_id,
                owner_user_id=owner_id,
                batch_id=batch.id,
                storage_key=key,
                content_type=image.content_type,
                filename=document.filename[:255],
                role="doc",
                byte_size=len(image.data),
                training_consent=batch.training_consent,
                source_kind="doc_embedded",
                width=image.width,
                height=image.height,
                source_label=(image.label + ("｜核心内容大图" if image.core else ""))[:255],
                sort_order=sort_order,
                content_hash=image.sha256,
            )
            sort_order += 1
            session.add(asset)
            stored_assets.append(asset)
            text = text.replace(placeholder, anchor_for_image(asset_id, image.label, image.width, image.height, image.core))
        for index, snapshot in enumerate(document.snapshots):
            placeholder = SNAPSHOT_PLACEHOLDER.format(index=index)
            if len(snapshots) >= BATCH_MAX_SNAPSHOTS:
                text = text.replace(placeholder, "")
                continue
            snap_id = f"{batch.id.hex[:8]}-{len(snapshots) + 1}"
            key = snapshot_key(batch.id, snap_id)
            await storage.put_bytes(key, snapshot.data, "image/png")
            snapshots.append(
                {
                    "snap_id": snap_id,
                    "storage_key": key,
                    "filename": document.filename,
                    "page_no": snapshot.page_no,
                    "width": snapshot.width,
                    "height": snapshot.height,
                    "reason": snapshot.reason,
                }
            )
            text = text.replace(placeholder, anchor_for_snapshot(snap_id, document.filename, snapshot.page_no, snapshot.reason))

        block = "\n".join(
            [f"## 文件 {doc_index}: {document.filename}", f"- 文件类型：{document.extension}", "", text or "未抽取到可用文本内容。"]
        ).strip()
        start = cursor + (2 if blocks else 0)
        blocks.append(block)
        cursor = start + len(block)
        documents_meta.append(
            {
                "filename": document.filename,
                "extension": document.extension,
                "sha256": document.content_sha256,
                "start": start,
                "end": cursor,
                "image_count": len(document.images),
                "snapshot_count": len(document.snapshots),
            }
        )
    await session.flush()
    return "\n\n".join(blocks), documents_meta


async def _mark_failed(
    batch_id: uuid.UUID,
    message: str,
    stored_assets: list[PageAsset],
    snapshots: list[dict[str, Any]],
    *,
    refund: bool,
) -> None:
    """解析失败或整批取消：记下原因、删原件和半途写入的图片，必要时退回一次匿名免费次数。"""
    async with _new_session() as session:
        batch = await session.get(GenerationBatch, batch_id)
        staged = list(batch.staged_uploads or []) if batch is not None else []
        if batch is not None:
            batch.parse_status = "failed"
            batch.parse_error = message
            batch.staged_uploads = []
        if stored_assets:
            asset_ids = [asset.id for asset in stored_assets]
            result = await session.execute(select(PageAsset).where(PageAsset.id.in_(asset_ids)))
            for asset in result.scalars():
                await session.delete(asset)
        await session.commit()
        if refund:
            await refund_free_trial_once(session, batch_id)
    await discard_staged(staged)
    await discard_snapshots(snapshots)
    await _delete_keys([asset.storage_key for asset in stored_assets])


async def refund_free_trial_once(session: AsyncSession, batch_id: uuid.UUID) -> bool:
    """整批都失败在资料阶段时退回一次匿名免费次数。free_trial_refunded 原子置位，保证只退一次。"""
    claimed = (
        await session.execute(
            update(GenerationBatch)
            .where(GenerationBatch.id == batch_id, GenerationBatch.free_trial_refunded.is_(False))
            .values(free_trial_refunded=True)
            .returning(GenerationBatch.conversation_id)
        )
    ).scalar_one_or_none()
    if claimed is None:
        await session.rollback()
        return False
    try:
        conversation = await session.get(Conversation, claimed)
        actor = await session.get(User, conversation.owner_user_id) if conversation else None
        if actor is not None and actor.is_anonymous:
            from app.services.billing import BillingService

            await BillingService(session).refund_anon_batch_started(actor)
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - 退回失败不掩盖资料阶段的失败本身
        await session.rollback()
        print(f"[billing] 资料阶段失败退回匿名免费次数失败 batch={batch_id}: {exc}")
        return False
    return True
