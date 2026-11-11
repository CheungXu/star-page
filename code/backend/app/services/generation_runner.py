"""后台生成执行器：模型调用在进程内常驻任务里跑完，和浏览器的事件流连接无关。

- 浏览器只订阅事件（见 generation_events.py），断开连接不影响生成。
- 两层名额：先过每个用户自己的上限，再过全站上限，都按先来后到排队；排队时写一条 queued 事件，轮到时写 started。
- 单进程假设：任务登记表和名额都在内存里，后端必须只跑一个 uvicorn worker。多 worker 需要给任务加 owner 与心跳再改扫尾。
- 进程退出时不改任务状态，留给下次启动的扫尾处理（近期中断的重跑一次，其余标失败）。
"""
from __future__ import annotations

import asyncio
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.entities import GenerationBatch, GenerationEvent, GenerationTask, Page, User
from app.services.generation_service import CANCELLED_USER_MESSAGE, GenerationService

# 协作式停止的宽限：超过这个时间模型还没吐出下一个 chunk，就直接取消协程。
HARD_CANCEL_GRACE_SECONDS = 3.0

CANCELLED_ERROR_MESSAGE = "用户停止了生成"
INTERRUPTED_ERROR_MESSAGE = "服务重启中断"
INTERRUPTED_USER_MESSAGE = "服务重启中断了这次生成，请重新发送"
RESTARTED_USER_MESSAGE = "服务重启，已自动重新生成"
UNFINISHED_ERROR_MESSAGE = "生成任务异常结束"
UNFINISHED_USER_MESSAGE = "页面生成失败，请稍后重试。"
GLOBAL_QUEUED_MESSAGE = "排队中 · 当前使用的人较多，轮到后自动开始"


def user_queued_message(limit: int) -> str:
    return f"排队中 · 你同时生成的模型已满 {limit} 个，前面的完成后自动开始"


def user_concurrency_limit(user: User) -> int:
    """用户同时生成的模型数上限：用户单独设置的优先，否则用默认值。会员按套餐放宽时改这里。"""
    return _resolve_user_limit(user.generation_concurrency)


def _resolve_user_limit(override: int | None) -> int:
    if override is not None and override > 0:
        return override
    return max(1, get_settings().generation_user_concurrency)


class FifoGate:
    """先来后到的名额闸门。上限可以随时调整，调大后排队的立即补上。

    enter() 是同步的：按调用顺序入队，保证同一用户的任务按提交顺序开始。
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(0, limit)
        self.active = 0
        self._waiters: deque[asyncio.Future[None]] = deque()

    def enter(self) -> asyncio.Future[None] | None:
        """拿名额。拿到返回 None；要排队就返回一个 future，轮到时它完成，名额已经记在 active 里。"""
        self._drop_cancelled()
        if self.active < self.limit and not self._waiters:
            self.active += 1
            return None
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(future)
        return future

    def abandon(self, ticket: asyncio.Future[None]) -> None:
        """排队的人不等了（被停止、出错）：还没轮到就作废排号，已经分到名额就还回去。"""
        if not ticket.cancel() and not ticket.cancelled():
            self.release()

    def release(self) -> None:
        self.active = max(0, self.active - 1)
        self._wake()

    def set_limit(self, limit: int) -> None:
        self.limit = max(0, limit)
        self._wake()

    def idle(self) -> bool:
        self._drop_cancelled()
        return self.active == 0 and not self._waiters

    def _drop_cancelled(self) -> None:
        while self._waiters and self._waiters[0].done():
            self._waiters.popleft()

    def _wake(self) -> None:
        while self._waiters and self.active < self.limit:
            future = self._waiters.popleft()
            if future.done():
                continue
            self.active += 1
            future.set_result(None)


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
    user_id: uuid.UUID | None = None


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
        self._global_gate: FifoGate | None = None
        self._user_gates: dict[uuid.UUID, FifoGate] = {}
        self._shutting_down = False

    # ---- 会话与名额 ----

    def _session(self) -> AsyncSession:
        if self._session_factory is None:
            from app.core.database import AsyncSessionLocal

            self._session_factory = AsyncSessionLocal
        return self._session_factory()

    def _global(self) -> FifoGate:
        if self._global_gate is None:
            self._global_gate = FifoGate(max(1, get_settings().generation_max_concurrency))
        return self._global_gate

    def _user_gate(self, user_id: uuid.UUID, limit: int) -> FifoGate:
        gate = self._user_gates.get(user_id)
        if gate is None:
            gate = FifoGate(limit)
            self._user_gates[user_id] = gate
        else:
            # 每次提交都按最新上限调整（例如刚升级会员），调大后排队的立即开始。
            gate.set_limit(limit)
        return gate

    def _release_user_gate(self, user_id: uuid.UUID, gate: FifoGate) -> None:
        gate.release()
        if gate.idle() and self._user_gates.get(user_id) is gate:
            self._user_gates.pop(user_id, None)

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

    def start(
        self,
        task_ids: Iterable[uuid.UUID],
        batch_id: uuid.UUID | None,
        *,
        user_id: uuid.UUID,
        user_limit: int,
    ) -> None:
        """把任务交给后台跑。重复调用同一个任务是安全的：已登记的跳过，数据库层还有原子认领兜底。

        user_id / user_limit 决定这个用户同时生成的上限，超出的按提交顺序排队。
        """
        if self._shutting_down:
            return
        gate = self._user_gate(user_id, user_limit)
        for task_id in task_ids:
            if task_id in self._tasks:
                continue
            key = _signal_key(task_id, batch_id)
            running = asyncio.create_task(self._drive(task_id, key, user_id, gate), name=f"generation-{task_id}")
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

    async def _drive(self, task_id: uuid.UUID, key: uuid.UUID, user_id: uuid.UUID, user_gate: FifoGate) -> None:
        holds_user = holds_global = False
        queued = False
        try:
            # 先过个人上限再过全站上限：个人名额没轮到的任务不占全站名额，不影响别人。
            # 这是协程的第一步，入队是同步的，同一用户的任务按提交顺序排。
            queued |= await self._acquire(
                user_gate, task_id, key, "user_limit", user_queued_message(user_gate.limit)
            )
            holds_user = True
            queued |= await self._acquire(self._global(), task_id, key, "global_limit", GLOBAL_QUEUED_MESSAGE)
            holds_global = True

            async with self._session() as session:
                service = GenerationService(session, cancel_check=lambda: task_id in self._cancel_requested)
                task = await service.claim_task(task_id)
                if task is None:
                    return
                self._claimed.add(task_id)
                if queued:
                    await service.record_event(task_id, "started", {"type": "started"})
                self.notify(key)
                async for _event in service.run_task(task):
                    self.notify(key)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底收尾在 finally 里
            print(f"[generation] 任务执行异常 task={task_id}: {exc!r}", flush=True)
        finally:
            if holds_global:
                self._global().release()
            if holds_user:
                self._release_user_gate(user_id, user_gate)
            elif user_gate.idle() and self._user_gates.get(user_id) is user_gate:
                self._user_gates.pop(user_id, None)
            # 进程退出时不写库，交给下次启动扫尾。没认领过的（排队中被停）由停止接口自己写成 cancelled。
            if not self._shutting_down and task_id in self._claimed:
                await self._finish_if_unfinished(task_id)

    async def _acquire(self, gate: FifoGate, task_id: uuid.UUID, key: uuid.UUID, reason: str, message: str) -> bool:
        """拿一个名额，返回是否排过队。排队时先写 queued 事件再等；中途被取消或出错就作废排号。"""
        ticket = gate.enter()
        if ticket is None:
            return False
        try:
            await self._record_queued(task_id, key, reason, message)
            await ticket
        except BaseException:
            gate.abandon(ticket)
            raise
        return True

    async def _record_queued(self, task_id: uuid.UUID, key: uuid.UUID, reason: str, message: str) -> None:
        """排队提示写不进去只影响显示，不能让任务因此卡在排队里。"""
        try:
            async with self._session() as session:
                await GenerationService(session).record_event(
                    task_id, "queued", {"type": "queued", "reason": reason, "message": message}
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[generation] 排队事件写库失败 task={task_id}: {exc!r}", flush=True)
            return
        self.notify(key)

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
            await _reset_interrupted_parses(session)
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
            await _discard_orphan_staging(session)
            resume = sorted([*plan.start, *plan.rerun], key=lambda item: item.created_at)
            limits = await _load_user_limits(session, {item.user_id for item in resume if item.user_id})

        # 按原来的提交顺序重新排队，个人上限照常生效。
        for item in resume:
            if item.user_id is None:
                continue
            self.start([item.task_id], item.batch_id, user_id=item.user_id, user_limit=limits[item.user_id])
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
            GenerationTask.requested_by_user_id,
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
            user_id=row.requested_by_user_id,
        )
        for row in result.all()
    ]


async def _load_user_limits(session: AsyncSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
    limits = {user_id: _resolve_user_limit(None) for user_id in user_ids}
    if user_ids:
        result = await session.execute(select(User.id, User.generation_concurrency).where(User.id.in_(user_ids)))
        for user_id, override in result.all():
            limits[user_id] = _resolve_user_limit(override)
    return limits


async def _reset_interrupted_parses(session: AsyncSession) -> None:
    """上个进程解析到一半退出：原件还在暂存区，状态退回 pending，重跑的任务会重新解析。"""
    await session.execute(
        update(GenerationBatch).where(GenerationBatch.parse_status == "running").values(parse_status="pending")
    )
    await session.commit()


async def _discard_orphan_staging(session: AsyncSession) -> None:
    """还没解析、但批次里已经没有要跑的任务了（扫尾标了失败）：删掉暂存原件，状态记为失败。"""
    from app.services.document_parser.pipeline import discard_staged

    live = select(GenerationTask.id).where(
        GenerationTask.batch_id == GenerationBatch.id, GenerationTask.status.in_(("pending", "running"))
    )
    result = await session.execute(
        select(GenerationBatch).where(GenerationBatch.parse_status == "pending", ~live.exists())
    )
    orphans = list(result.scalars())
    staged = [item for batch in orphans for item in (batch.staged_uploads or [])]
    for batch in orphans:
        batch.parse_status = "failed"
        batch.parse_error = INTERRUPTED_USER_MESSAGE
        batch.staged_uploads = []
    await session.commit()
    await discard_staged(staged)


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
        task.extracted_context = None
        page = await session.get(Page, task.page_id)
        if page is not None and page.deleted_at is None:
            page.status = "generating"
        payload: dict[str, Any] = {"type": "restarted", "message": RESTARTED_USER_MESSAGE, "model_key": task.model_key}
        session.add(GenerationEvent(task_id=task.id, event_type="restarted", payload=payload))
    await session.commit()


generation_runner = GenerationRunner()
