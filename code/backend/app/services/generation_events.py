"""生成事件流：只读数据库里的事件，按任务游标续传，由后台执行器唤醒。订阅断开不影响生成。

为什么每个任务一个游标：sequence 全局递增，但多个任务并发写入时提交顺序和 sequence 顺序不一致，
只用一个全局游标会永久漏掉晚提交的小号事件。单个任务的事件是逐条提交的，任务内 sequence 与提交顺序一致。
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import GenerationEvent, GenerationTask
from app.services.generation_runner import GenerationRunner
from app.services.generation_service import CANCELLED_USER_MESSAGE, TERMINAL_EVENT_TYPES, TERMINAL_STATUSES
from app.services.sse import SseEvent

HEARTBEAT_SECONDS = 15.0
# 被唤醒后稍等再读库，把这段时间里各模型写入的事件合成一次查询。
WAKE_DEBOUNCE_SECONDS = 0.2
# 一次最多读这么多行；回放很长的任务时分几块发，每块结束带一次 id 供续传。
CHUNK_LIMIT = 2000


@dataclass(frozen=True)
class EventRow:
    task_id: uuid.UUID
    sequence: int
    event_type: str
    payload: dict[str, Any]


class Heartbeat:
    """空闲时发给浏览器的心跳占位，由路由格式化成 SSE 注释行。"""


HEARTBEAT = Heartbeat()


def ordered_task_ids(task_ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    """游标里任务的固定顺序，两次连接之间必须一致。"""
    return sorted(set(task_ids), key=str)


def encode_cursor(task_ids: Sequence[uuid.UUID], cursors: dict[uuid.UUID, int]) -> str:
    return ".".join(str(cursors.get(task_id, 0)) for task_id in task_ids)


def decode_cursor(raw: str | None, task_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    """解析 Last-Event-ID。格式是按 ordered_task_ids 顺序、用点连接的各任务游标；看不懂就从头开始。"""
    cursors = {task_id: 0 for task_id in task_ids}
    text = (raw or "").strip()
    if not text:
        return cursors
    parts = text.split(".")
    if not all(part.isdigit() for part in parts):
        return cursors
    if len(parts) == 1 and len(task_ids) != 1:
        # 单个数字（旧的逐任务事件流留下的 id）：当作所有任务的共同起点。
        value = int(parts[0])
        return {task_id: value for task_id in task_ids}
    if len(parts) != len(task_ids):
        return cursors
    return {task_id: int(part) for task_id, part in zip(task_ids, parts)}


def coalesce_events(rows: Sequence[EventRow]) -> list[SseEvent]:
    """把同一任务里连续的 reasoning_delta 合成一条，同一任务内的先后顺序不变。

    合并后的事件放在它第一段的位置上；不同任务之间的相对顺序可能变化，前端按 task_id 分发，不受影响。
    """
    out: list[SseEvent] = []
    open_reasoning: dict[uuid.UUID, int] = {}
    for row in rows:
        task_key = str(row.task_id)
        if row.event_type == "reasoning_delta":
            text = str(row.payload.get("text") or "")
            index = open_reasoning.get(row.task_id)
            if index is not None:
                merged = out[index]
                out[index] = SseEvent(merged.event, {**merged.data, "text": merged.data["text"] + text})
                continue
            open_reasoning[row.task_id] = len(out)
            out.append(SseEvent("reasoning_delta", {"type": "reasoning_delta", "text": text, "task_id": task_key}))
            continue
        open_reasoning.pop(row.task_id, None)
        payload = dict(row.payload)
        # 实际金额只留在版本和流水里，发给浏览器的事件里去掉。
        payload.pop("cost", None)
        payload["task_id"] = task_key
        out.append(SseEvent(row.event_type, payload))
    return out


def _synthesize_terminal(task: Any) -> SseEvent:
    """老数据：任务已是终态却没有收尾事件（旧代码先改状态后写事件）。按状态补一条，前端才能结束。"""
    task_key = str(task.id)
    if task.status == "succeeded":
        return SseEvent(
            "completed",
            {"type": "completed", "page_id": str(task.page_id), "model_key": task.model_key, "task_id": task_key},
        )
    if task.status == "cancelled":
        return SseEvent(
            "cancelled",
            {"type": "cancelled", "message": CANCELLED_USER_MESSAGE, "model_key": task.model_key, "task_id": task_key},
        )
    return SseEvent(
        "failed",
        {"type": "failed", "message": "页面生成失败，请重新创建。", "model_key": task.model_key, "task_id": task_key},
    )


async def stream_task_events(
    *,
    session_factory: Callable[[], AsyncSession],
    runner: GenerationRunner,
    task_ids: Sequence[uuid.UUID],
    signal_key: uuid.UUID,
    last_event_id: str | None = None,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    debounce_seconds: float = WAKE_DEBOUNCE_SECONDS,
) -> AsyncIterator[SseEvent | Heartbeat]:
    """订阅一组任务的事件，直到每个任务都发出收尾事件（completed / failed / cancelled），最后发 batch_done。"""
    ids = ordered_task_ids(task_ids)
    cursors = decode_cursor(last_event_id, ids)
    finished: set[uuid.UUID] = set()
    signal = runner.acquire_signal(signal_key)
    try:
        if any(cursors.values()):
            # 续传：游标之前已经收到过收尾事件的任务不会再出现，先记下来。
            async with session_factory() as session:
                result = await session.execute(
                    select(GenerationEvent.task_id).where(
                        GenerationEvent.event_type.in_(TERMINAL_EVENT_TYPES),
                        or_(
                            *(
                                and_(GenerationEvent.task_id == task_id, GenerationEvent.sequence <= cursor)
                                for task_id, cursor in cursors.items()
                            )
                        ),
                    )
                )
                finished.update(result.scalars().all())

        while True:
            remaining = [task_id for task_id in ids if task_id not in finished]
            if not remaining:
                yield SseEvent("batch_done", {"type": "batch_done"})
                return

            # 先取 waiter 再读库：读库之后才到的通知也能把下面的等待唤醒。
            waiter = signal.waiter()
            synthesized: list[SseEvent] = []
            async with session_factory() as session:
                result = await session.execute(
                    select(
                        GenerationEvent.task_id,
                        GenerationEvent.sequence,
                        GenerationEvent.event_type,
                        GenerationEvent.payload,
                    )
                    .where(
                        or_(
                            *(
                                and_(GenerationEvent.task_id == task_id, GenerationEvent.sequence > cursors[task_id])
                                for task_id in remaining
                            )
                        )
                    )
                    .order_by(GenerationEvent.sequence.asc())
                    .limit(CHUNK_LIMIT)
                )
                rows = [EventRow(row.task_id, row.sequence, row.event_type, dict(row.payload or {})) for row in result.all()]
                read_again = len(rows) == CHUNK_LIMIT
                for row in rows:
                    cursors[row.task_id] = max(cursors[row.task_id], row.sequence)
                    if row.event_type in TERMINAL_EVENT_TYPES:
                        finished.add(row.task_id)

                unfinished = [task_id for task_id in remaining if task_id not in finished]
                if unfinished and not read_again:
                    tasks_result = await session.execute(
                        select(
                            GenerationTask.id, GenerationTask.status, GenerationTask.page_id, GenerationTask.model_key
                        ).where(GenerationTask.id.in_(unfinished))
                    )
                    for task in tasks_result.all():
                        if task.status not in TERMINAL_STATUSES:
                            continue
                        pending_terminal = await session.scalar(
                            select(GenerationEvent.sequence)
                            .where(
                                GenerationEvent.task_id == task.id,
                                GenerationEvent.sequence > cursors[task.id],
                                GenerationEvent.event_type.in_(TERMINAL_EVENT_TYPES),
                            )
                            .limit(1)
                        )
                        if pending_terminal is not None:
                            # 终态和收尾事件同一个事务提交：事件刚写进来还没读到，马上再读一轮。
                            read_again = True
                            continue
                        synthesized.append(_synthesize_terminal(task))
                        finished.add(task.id)

            events = coalesce_events(rows)
            if events:
                last = events[-1]
                events[-1] = SseEvent(last.event, last.data, id=encode_cursor(ids, cursors))
            for event in [*events, *synthesized]:
                yield event

            if read_again or all(task_id in finished for task_id in ids):
                continue
            try:
                await asyncio.wait_for(waiter.wait(), timeout=heartbeat_seconds)
            except TimeoutError:
                yield HEARTBEAT
                continue
            if debounce_seconds > 0:
                await asyncio.sleep(debounce_seconds)
    finally:
        runner.release_signal(signal_key, signal)
