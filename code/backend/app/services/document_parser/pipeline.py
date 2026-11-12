"""批次级后台解析：创建请求只暂存原件，第一个开跑的模型拉起解析，同批其他模型等同一个结果。

- 解析是独立的 asyncio.Task，不属于任何一个模型的协程：停掉某个模型不会打断解析，
  只有同批已经没有排队或在跑的任务时才取消解析。
- 同一进程内用 _jobs 合并；跨 worker 靠 parse_status 原子抢占：抢到的解析并定时续期，其余的每秒查一次结果。
  做解析的 worker 续期超时（不在了），下一个查结果的 worker 接手重做。
- 原件只在解析期间存在：成功、失败、取消都会删掉；进程退出时没做完的不删，留给接手的 worker。
  整页快照存私有键，不进 page_assets。
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.executors import parse_executor
from app.models.entities import Conversation, GenerationBatch, GenerationTask, PageAsset, User
from app.services.document_parser.errors import DocumentParseError
from app.services.document_parser.limits import BATCH_MAX_SNAPSHOTS, PARSE_TIMEOUT_SECONDS
from app.services.document_parser.parse import parse_document
from app.services.document_parser.types import IMAGE_PLACEHOLDER, SNAPSHOT_PLACEHOLDER, ParsedDocument, StagedDocument
from app.services.storage.factory import create_storage_provider

STAGING_PREFIX = "batch-uploads"
SNAPSHOT_PREFIX = "batch-private"
CANCELLED_PARSE_MESSAGE = "已停止生成"
# 别的 worker 在解析时，多久查一次它做完没有。
PARSE_WAIT_POLL_SECONDS = 1.0
PROCESS_WORKER_ID = f"{socket.gethostname()[:32]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


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
    # 等待中被停掉的任务。它们的终态可能还没写进库，判断「批次是否还有人要」时要排除。
    abandoned_by: set[uuid.UUID] = field(default_factory=set)


_jobs: dict[uuid.UUID, _ParseJob] = {}
_background: set[asyncio.Task[None]] = set()
_session_factory: Callable[[], AsyncSession] | None = None
_handing_over = False


def hand_over_on_exit() -> None:
    """进程退出前调用：之后被取消的解析不记失败，交给别的 worker 接手。"""
    global _handing_over
    _handing_over = True


def active_parse_jobs() -> int:
    return len(_jobs)


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


def _start_job(batch_id: uuid.UUID) -> _ParseJob:
    job = _ParseJob(task=asyncio.create_task(_run_parse(batch_id), name=f"parse-{batch_id}"))
    _jobs[batch_id] = job

    def done(task: asyncio.Task[None]) -> None:
        # 解析结束才注销，期间后到的模型复用同一个任务，不会重复解析。
        if _jobs.get(batch_id) is job:
            _jobs.pop(batch_id, None)
        if not task.cancelled():
            task.exception()  # 没人等的时候也取走异常，免得事件循环报未读取

    job.task.add_done_callback(done)
    return job


async def wait_for_batch_parse(batch_id: uuid.UUID, task_id: uuid.UUID | None = None) -> None:
    """等本批解析完成。失败抛 DocumentParseError。

    等待者被停掉不会直接取消解析：同批还有排队或在跑的任务（例如个人上限下还没轮到的模型）时，
    解析继续跑完供它们使用；只有整批都没人要了才取消。
    """
    job = _jobs.get(batch_id) or _start_job(batch_id)
    job.waiters += 1
    try:
        await asyncio.shield(job.task)
    except asyncio.CancelledError:
        if task_id is not None:
            job.abandoned_by.add(task_id)
        if job.waiters <= 1 and not job.task.done():
            checker = asyncio.create_task(_cancel_if_abandoned(batch_id, job))
            _background.add(checker)
            checker.add_done_callback(_background.discard)
        raise
    finally:
        job.waiters -= 1


async def _cancel_if_abandoned(batch_id: uuid.UUID, job: _ParseJob) -> None:
    try:
        others_live = await _batch_has_live_tasks(batch_id, exclude=job.abandoned_by)
    except Exception as exc:  # noqa: BLE001 - 查不到就不取消，最坏是多解析一次
        print(f"[parse] 判断批次是否还有任务失败 batch={batch_id}: {exc!r}")
        return
    if not others_live and job.waiters <= 0 and not job.task.done():
        job.task.cancel()


async def _batch_has_live_tasks(batch_id: uuid.UUID, *, exclude: set[uuid.UUID]) -> bool:
    async with _new_session() as session:
        query = select(GenerationTask.id).where(
            GenerationTask.batch_id == batch_id, GenerationTask.status.in_(("pending", "running"))
        )
        if exclude:
            query = query.where(GenerationTask.id.not_in(exclude))
        return (await session.execute(query.limit(1))).first() is not None


async def _run_parse(batch_id: uuid.UUID) -> None:
    """同批的模型可能分在不同 worker 上：抢到解析的去做，其余的等它的结果。做解析的 worker 不在了就有人接手重做。"""
    while True:
        outcome = await _claim_parse(batch_id)
        if outcome == "done":
            return
        if outcome == "mine":
            if await _parse_claimed(batch_id):
                return
            # 做到一半被别的 worker 接手了（本进程续期中断过）：结果不写，回去等它的。
            continue
        await asyncio.sleep(PARSE_WAIT_POLL_SECONDS)


async def _claim_parse(batch_id: uuid.UUID) -> str:
    """返回 done（不用解析或已解析完）、mine（本进程抢到了）、busy（别的 worker 在做）。解析失败直接抛错。"""
    lease = timedelta(seconds=max(1.0, get_settings().generation_worker_lease_seconds))
    async with _new_session() as session:
        claimed = (
            await session.execute(
                update(GenerationBatch)
                .where(
                    GenerationBatch.id == batch_id,
                    or_(
                        GenerationBatch.parse_status == "pending",
                        (GenerationBatch.parse_status == "running")
                        & or_(
                            GenerationBatch.parse_heartbeat_at.is_(None),
                            GenerationBatch.parse_heartbeat_at < func.now() - lease,
                        ),
                    ),
                )
                .values(parse_status="running", parse_worker_id=PROCESS_WORKER_ID, parse_heartbeat_at=func.now())
                .returning(GenerationBatch.id)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()
        if claimed is not None:
            await session.commit()
            return "mine"
        row = (
            await session.execute(
                select(GenerationBatch.parse_status, GenerationBatch.parse_error).where(GenerationBatch.id == batch_id)
            )
        ).first()
        await session.commit()
    if row is None or row.parse_status in {"none", "succeeded"}:
        return "done"
    if row.parse_status == "failed":
        raise DocumentParseError(row.parse_error or "文件解析失败，请重新上传")
    return "busy"


async def _parse_heartbeat(batch_id: uuid.UUID) -> None:
    interval = max(0.5, get_settings().generation_heartbeat_seconds)
    while True:
        await asyncio.sleep(interval)
        try:
            async with _new_session() as session:
                await session.execute(
                    update(GenerationBatch)
                    .where(
                        GenerationBatch.id == batch_id,
                        GenerationBatch.parse_status == "running",
                        GenerationBatch.parse_worker_id == PROCESS_WORKER_ID,
                    )
                    .values(parse_heartbeat_at=func.now())
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - 续期失败下一轮再来
            print(f"[parse] 解析续期失败 batch={batch_id}: {exc!r}", flush=True)


async def _hand_over_parse(batch_id: uuid.UUID) -> None:
    """进程退出时解析没做完：状态留在 running、作废续期，原件不删，别的 worker 马上可以接手重做。"""
    try:
        async with _new_session() as session:
            await session.execute(
                update(GenerationBatch)
                .where(GenerationBatch.id == batch_id, GenerationBatch.parse_worker_id == PROCESS_WORKER_ID)
                .values(parse_heartbeat_at=None)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[parse] 交出解析失败 batch={batch_id}: {exc!r}", flush=True)


async def _parse_claimed(batch_id: uuid.UUID) -> bool:
    """返回 False 表示解析已被别的 worker 接手，本进程什么都没写。"""
    heartbeat = asyncio.create_task(_parse_heartbeat(batch_id), name=f"parse-heartbeat-{batch_id}")
    try:
        return await _parse_owned(batch_id)
    finally:
        heartbeat.cancel()


async def _lock_if_owner(session: AsyncSession, batch_id: uuid.UUID) -> bool:
    """锁住批次行并确认解析仍归本进程。锁到调用方提交为止，接手和本次写入不会交错。"""
    row = (
        await session.execute(
            select(GenerationBatch.parse_status, GenerationBatch.parse_worker_id)
            .where(GenerationBatch.id == batch_id)
            .with_for_update()
        )
    ).first()
    return row is not None and row.parse_status == "running" and row.parse_worker_id == PROCESS_WORKER_ID


async def _parse_owned(batch_id: uuid.UUID) -> bool:
    async with _new_session() as session:
        batch = await session.get(GenerationBatch, batch_id, populate_existing=True)
        if batch is None:
            return True

        stored_assets: list[PageAsset] = []
        snapshots: list[dict[str, Any]] = []
        try:
            # 归属和原件清单先读出来并提交。后面的下载、解析、上传不再占着连接。
            owner_id = await _batch_owner_id(session, batch)
            staged = list(batch.staged_uploads or [])
            await session.commit()

            parsed = await _parse_staged(staged)
            text, documents_meta = await _store_outputs(batch, owner_id, parsed, stored_assets, snapshots)
            if not await _lock_if_owner(session, batch_id):
                await session.rollback()
                await _discard_lost_outputs(stored_assets)
                return False
            for asset in stored_assets:
                session.add(asset)
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
            return True
        except asyncio.CancelledError:
            if _handing_over:
                # 先删再交出：交出后接手的 worker 会按同样的键重新上传快照。
                await _delete_keys([asset.storage_key for asset in stored_assets] + [item.get("storage_key") for item in snapshots])
                await asyncio.shield(_hand_over_parse(batch_id))
            else:
                await asyncio.shield(_mark_failed(batch_id, CANCELLED_PARSE_MESSAGE, stored_assets, snapshots, refund=False))
            raise
        except DocumentParseError as exc:
            await session.rollback()
            if not await _mark_failed(batch_id, exc.user_message, stored_assets, snapshots, refund=True):
                return False
            raise
        except Exception as exc:  # noqa: BLE001 - 解析器抛什么都要落成用户可读的失败
            await session.rollback()
            message = "文件解析失败，请确认文件未加密且内容可读取"
            if not await _mark_failed(batch_id, message, stored_assets, snapshots, refund=True):
                return False
            raise DocumentParseError(message, detail=f"{type(exc).__name__}: {exc}") from exc


async def _discard_lost_outputs(stored_assets: list[PageAsset]) -> None:
    """解析被接手时只删本进程上传的配图（键带随机 ID）。快照键按批次固定，接手的 worker 会写同一个键，不能删。"""
    await _delete_keys([asset.storage_key for asset in stored_assets])


async def _parse_staged(staged: list[dict[str, Any]]) -> list[ParsedDocument]:
    storage = create_storage_provider()
    parsed: list[ParsedDocument] = []
    for item in staged:
        filename = item.get("filename") or "未命名文件"
        data = await storage.get_bytes(item["storage_key"])
        try:
            # 线程里的解析超时后无法强行中止，只能不再等它；上限保证了单个文件的工作量有界。
            loop = asyncio.get_running_loop()
            document = await asyncio.wait_for(
                loop.run_in_executor(parse_executor(), parse_document, filename, item.get("extension") or "", data),
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
    batch: GenerationBatch,
    owner_id: uuid.UUID,
    parsed: list[ParsedDocument],
    stored_assets: list[PageAsset],
    snapshots: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """配图和快照先上传，占位换成锚点。这里不碰数据库；调用方再用一次短事务写入。"""
    storage = create_storage_provider()
    # 同一批里多份文档出现同一张图：只存一份，后面的文档复用这个资源 ID 写锚点。
    stored_by_hash: dict[str, uuid.UUID] = {}
    blocks: list[str] = []
    documents_meta: list[dict[str, Any]] = []
    cursor = 0
    sort_order = 0
    for doc_index, document in enumerate(parsed, start=1):
        text = document.text
        for index, image in enumerate(document.images):
            placeholder = IMAGE_PLACEHOLDER.format(index=index)
            existing = stored_by_hash.get(image.sha256)
            if existing is not None:
                text = text.replace(placeholder, anchor_for_image(existing, image.label, image.width, image.height, image.core))
                continue
            asset_id = uuid.uuid4()
            stored_by_hash[image.sha256] = asset_id
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
    return "\n\n".join(blocks), documents_meta


async def _mark_failed(
    batch_id: uuid.UUID,
    message: str,
    stored_assets: list[PageAsset],
    snapshots: list[dict[str, Any]],
    *,
    refund: bool,
) -> bool:
    """解析失败或整批取消：记下原因、删原件和半途写入的图片，必要时退回一次匿名免费次数。

    解析已被别的 worker 接手时只删本进程上传的配图，返回 False。
    """
    async with _new_session() as session:
        if not await _lock_if_owner(session, batch_id):
            await session.rollback()
            await _discard_lost_outputs(stored_assets)
            return False
        batch = await session.get(GenerationBatch, batch_id, populate_existing=True)
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
    return True


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
