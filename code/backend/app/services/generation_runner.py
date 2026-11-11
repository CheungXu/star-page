"""后台生成执行器：模型调用在进程内常驻任务里跑完，和浏览器的事件流连接无关。

- 浏览器只订阅事件（见 generation_events.py），断开连接不影响生成。
- 单进程假设：任务登记表在内存里，后端必须只跑一个 uvicorn worker。多 worker 需要给任务加 owner 与心跳再改扫尾。
- 进程退出时不改任务状态，留给下次启动的扫尾处理（近期中断的重跑一次，其余标失败）。
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.entities import GenerationEvent, GenerationTask, Page
from app.services.generation_service import CANCELLED_USER_MESSAGE, GenerationService

# 协作式停止的宽限：超过这个时间模型还没吐出下一个 chunk，就直接取消协程。
HARD_CANCEL_GRACE_SECONDS = 3.0

CANCELLED_ERROR_MESSAGE = "用户停止了生成"
INTERRUPTED_ERROR_MESSAGE = "服务重启中断"
INTERRUPTED_USER_MESSAGE = "服务重启中断了这次生成，请重新发送"
RESTARTED_USER_MESSAGE = "服务重启，已自动重新生成"
UNFINISHED_ERROR_MESSAGE = "生成任务异常结束"
UNFINISHED_USER_MESSAGE = "页面生成失败，请稍后重试。"


class BatchSignal:
    """一个批次的事件唤醒器。订阅者要先取 waiter 再读库，这样读库和等待之间到来的通知不会漏掉。"""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.refs = 0

    def waiter(self) -> asyncio.Event:
        return self._event

    def notify(self) -> None:
        self._event.set()
        self._event = asyncio.Event()


@dataclass(frozen=True)
class InterruptedTask:
    """启动扫尾看到的一个未结束任务。"""

    task_id: uuid.UUID
    batch_id: uuid.UUID | None
    status: str
    retry_count: int
    created_at: datetime
    started_at: datetime | None
    last_event_at: datetime | None


@dataclass
class SweepPlan:
    start: list[InterruptedTask] = field(default_factory=list)  # 还没开始过，直接排队
    rerun: list[InterruptedTask] = field(default_factory=list)  # 中途被打断，清掉事件从头重跑
    fail: list[InterruptedTask] = field(default_factory=list)  # 太久以前的或已经重跑过一次的


def plan_sweep(tasks: Sequence[InterruptedTask], *, now: datetime, window: timedelta) -> SweepPlan:
    """按最近活动时间决定每个中断任务怎么收尾。纯函数，便于单测。"""
    plan = SweepPlan()
    for item in tasks:
        moments = [moment for moment in (item.last_event_at, item.started_at, item.created_at) if moment is not None]
        last_activity = max(moments)
        if now - last_activity > window:
            plan.fail.append(item)
        elif item.status == "pending" and item.started_at is None:
            plan.start.append(item)
        elif item.retry_count < 1:
            plan.rerun.append(item)
        else:
            plan.fail.append(item)
    return plan


def _signal_key(task_id: uuid.UUID, batch_id: uuid.UUID | None) -> uuid.UUID:
    """事件流按批次订阅；没有批次的老任务退化为按任务订阅。"""
    return batch_id or task_id


class GenerationRunner:
    def __init__(self, session_factory: Callable[[], AsyncSession] | None = None) -> None:
        self._session_factory = session_factory
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._claimed: set[uuid.UUID] = set()
        self._cancel_requested: set[uuid.UUID] = set()
        self._signals: dict[uuid.UUID, BatchSignal] = {}
        self._semaphore: asyncio.Semaphore | None = None
        self._shutting_down = False

    # ---- 会话与并发 ----

    def _session(self) -> AsyncSession:
        if self._session_factory is None:
            from app.core.database import AsyncSessionLocal

            self._session_factory = AsyncSessionLocal
        return self._session_factory()

    def _limit(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max(1, get_settings().generation_max_concurrency))
        return self._semaphore

    # ---- 订阅唤醒 ----

    def acquire_signal(self, key: uuid.UUID) -> BatchSignal:
        signal = self._signals.get(key)
        if signal is None:
            signal = BatchSignal()
            self._signals[key] = signal
        signal.refs += 1
        return signal

    def release_signal(self, key: uuid.UUID, signal: BatchSignal) -> None:
        signal.refs -= 1
        if signal.refs <= 0 and self._signals.get(key) is signal:
            self._signals.pop(key, None)

    def notify(self, key: uuid.UUID) -> None:
        signal = self._signals.get(key)
        if signal is not None:
            signal.notify()

    # ---- 启动与停止 ----

    def is_active(self, task_id: uuid.UUID) -> bool:
        return task_id in self._tasks

    def start(self, task_ids: Iterable[uuid.UUID], batch_id: uuid.UUID | None) -> None:
        """把任务交给后台跑。重复调用同一个任务是安全的：已登记的跳过，数据库层还有原子认领兜底。"""
        if self._shutting_down:
            return
        for task_id in task_ids:
            if task_id in self._tasks:
                continue
            key = _signal_key(task_id, batch_id)
            running = asyncio.create_task(self._drive(task_id, key), name=f"generation-{task_id}")
            self._tasks[task_id] = running
            # 清理放在 done 回调里：开跑前就被取消的协程不会执行自己的 finally。
            running.add_done_callback(lambda _done, task_id=task_id, key=key: self._forget(task_id, key))

    def _forget(self, task_id: uuid.UUID, key: uuid.UUID) -> None:
        self._tasks.pop(task_id, None)
        self._claimed.discard(task_id)
        self._cancel_requested.discard(task_id)
        self.notify(key)

    async def cancel(self, task_id: uuid.UUID) -> bool:
        """请求停止一个正在调用模型的任务，返回 True。

        返回 False 表示这个任务不在本进程里调用模型（没登记，或还在排队、已被直接取消），
        调用方需要自己用 finish_unfinished_task 把状态写成 cancelled。
        """
        running = self._tasks.get(task_id)
        if running is None or running.done():
            return False
        self._cancel_requested.add(task_id)
        if task_id not in self._claimed:
            running.cancel()
            return False
        asyncio.get_running_loop().call_later(HARD_CANCEL_GRACE_SECONDS, self._hard_cancel, task_id, running)
        return True

    def _hard_cancel(self, task_id: uuid.UUID, running: asyncio.Task[None]) -> None:
        if not running.done() and task_id in self._cancel_requested:
            running.cancel()

    async def shutdown(self) -> None:
        """进程退出：取消所有协程但不改数据库状态，交给下次启动扫尾重跑。"""
        self._shutting_down = True
        running = [task for task in self._tasks.values() if not task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.wait(running, timeout=3)

    async def _drive(self, task_id: uuid.UUID, key: uuid.UUID) -> None:
        try:
            async with self._limit():
                async with self._session() as session:
                    service = GenerationService(session, cancel_check=lambda: task_id in self._cancel_requested)
                    task = await service.claim_task(task_id)
                    if task is None:
                        return
                    self._claimed.add(task_id)
                    self.notify(key)
                    async for _event in service.run_task(task):
                        self.notify(key)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底收尾在 finally 里
            print(f"[generation] 任务执行异常 task={task_id}: {exc!r}", flush=True)
        finally:
            # 进程退出时不写库，交给下次启动扫尾；只有认领过或被要求停止的任务才需要补写终态。
            if not self._shutting_down and (task_id in self._claimed or task_id in self._cancel_requested):
                await self._finish_if_unfinished(task_id)

    async def _finish_if_unfinished(self, task_id: uuid.UUID) -> None:
        """生成器没把任务写到终态（被硬取消、数据库异常等）时，用新会话补写。"""
        cancelled = task_id in self._cancel_requested
        try:
            async with self._session() as session:
                await asyncio.shield(
                    GenerationService(session).finish_unfinished_task(
                        task_id,
                        status="cancelled" if cancelled else "failed",
                        message=CANCELLED_ERROR_MESSAGE if cancelled else UNFINISHED_ERROR_MESSAGE,
                        user_message=CANCELLED_USER_MESSAGE if cancelled else UNFINISHED_USER_MESSAGE,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[generation] 任务收尾写库失败 task={task_id}: {exc!r}", flush=True)

    # ---- 启动扫尾 ----

    async def startup_sweep(self) -> SweepPlan:
        """处理上一个进程留下的 pending/running 任务。必须在接收请求前、单 worker 下执行。"""
        settings = get_settings()
        window = timedelta(minutes=max(0, settings.generation_resume_window_minutes))
        async with self._session() as session:
            items = await _load_unfinished(session)
            plan = plan_sweep(items, now=datetime.now(UTC), window=window)
            await _reset_for_rerun(session, plan.rerun)
            service = GenerationService(session)
            for item in plan.fail:
                await service.finish_unfinished_task(
                    item.task_id,
                    status="failed",
                    message=INTERRUPTED_ERROR_MESSAGE,
                    user_message=INTERRUPTED_USER_MESSAGE,
                )
            for batch_id in {item.batch_id for item in plan.rerun}:
                await service.recompute_batch_status(batch_id)

        by_batch: dict[uuid.UUID | None, list[uuid.UUID]] = {}
        for item in [*plan.start, *plan.rerun]:
            by_batch.setdefault(item.batch_id, []).append(item.task_id)
        for batch_id, task_ids in by_batch.items():
            self.start(task_ids, batch_id)
        print(
            f"[generation] 启动扫尾：排队 {len(plan.start)}，重跑 {len(plan.rerun)}，标失败 {len(plan.fail)}",
            flush=True,
        )
        return plan


async def _load_unfinished(session: AsyncSession) -> list[InterruptedTask]:
    last_event_at = (
        select(func.max(GenerationEvent.created_at))
        .where(GenerationEvent.task_id == GenerationTask.id)
        .correlate(GenerationTask)
        .scalar_subquery()
    )
    result = await session.execute(
        select(
            GenerationTask.id,
            GenerationTask.batch_id,
            GenerationTask.status,
            GenerationTask.retry_count,
            GenerationTask.created_at,
            GenerationTask.started_at,
            last_event_at.label("last_event_at"),
        ).where(GenerationTask.status.in_(("pending", "running")))
    )
    return [
        InterruptedTask(
            task_id=row.id,
            batch_id=row.batch_id,
            status=row.status,
            retry_count=int(row.retry_count or 0),
            created_at=row.created_at,
            started_at=row.started_at,
            last_event_at=row.last_event_at,
        )
        for row in result.all()
    ]


async def _reset_for_rerun(session: AsyncSession, items: Sequence[InterruptedTask]) -> None:
    """清掉上次的事件和半成品，任务回到 pending，记一条 restarted 让前端清空旧进度。"""
    if not items:
        return
    task_ids = [item.task_id for item in items]
    await session.execute(delete(GenerationEvent).where(GenerationEvent.task_id.in_(task_ids)))
    for task_id in task_ids:
        task = await session.get(GenerationTask, task_id)
        if task is None:
            continue
        task.status = "pending"
        task.retry_count = (task.retry_count or 0) + 1
        task.started_at = None
        task.finished_at = None
        task.error_message = None
        task.model_output_text = None
        task.output_html_storage_key = None
        page = await session.get(Page, task.page_id)
        if page is not None and page.deleted_at is None:
            page.status = "generating"
        payload: dict[str, Any] = {"type": "restarted", "message": RESTARTED_USER_MESSAGE, "model_key": task.model_key}
        session.add(GenerationEvent(task_id=task.id, event_type="restarted", payload=payload))
    await session.commit()


generation_runner = GenerationRunner()
