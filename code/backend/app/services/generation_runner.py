"""后台生成执行器：模型调用在进程内常驻任务里跑完，和浏览器的事件流连接无关。

- 浏览器只订阅事件（见 generation_events.py），断开连接不影响生成。
- 名额在库里：个人上限和全站上限都按 status = 'running' 的行数算。认领在一个很短的事务里做，
  事务开头拿一把咨询锁，多个 worker 的认领排着做，不会一起超额。拿不到名额的任务留在 pending，
  按提交顺序排队，并写一条 queued 事件；轮到时认领成 running，写 started。
- 任务结束（成功、失败、停止）名额就自然空出来，不会因为进程崩溃漏还。结束后发一次派发通知，空闲的 worker 立刻补领。
- 认领时记下 worker_id，每隔 GENERATION_HEARTBEAT_SECONDS 续期 heartbeat_at。超过
  GENERATION_WORKER_LEASE_SECONDS 没续期的任务，别的 worker（或重启后的自己）接手：近期的从头重跑一次，有停止请求的写成已停止，其余标失败。
  在线 worker 手上的任务不会被碰。
- web 进程不跑生成，只把新批次和停止请求通过 generation_bus 通知 worker，worker 再定时扫一遍库兜底。
- 退出时先排空：不再领新任务，手上的跑完再走（见 drain）。超时才取消协程，并把这些任务的续期作废，让别的 worker 马上接手。
"""
from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.entities import GenerationBatch, GenerationEvent, GenerationTask, Page, User
from app.services.generation_bus import CHANNEL_DISPATCH, GenerationBus
from app.services.generation_service import CANCELLED_USER_MESSAGE, TERMINAL_STATUSES, GenerationService

# 一次认领最多看这么多个排队任务；每个用户最多看前 CLAIM_PER_USER_WINDOW 个，免得一个人排的几百个挡住别人。
CLAIM_SCAN_LIMIT = 500
CLAIM_PER_USER_WINDOW = 64

# 咨询锁的键：认领、接手失联任务各一把。只在很短的事务里持有，事务结束自动释放。
CLAIM_LOCK_KEY = 0x5350_434C
SWEEP_LOCK_KEY = 0x5350_5357

# 停止通知可能比本进程登记认领还早到（认领已提交、还没回到这里），记一会儿，登记时再处理。
EARLY_CANCEL_TTL_SECONDS = 60.0

# 作废续期时写的时间：任何 worker 下一次扫描都会把它当作失联任务接手。
EXPIRED_LEASE = datetime(1970, 1, 1, tzinfo=UTC)

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


def new_worker_id() -> str:
    return f"{socket.gethostname()[:32]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


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
    """扫尾看到的一个未结束任务。"""

    task_id: uuid.UUID
    batch_id: uuid.UUID | None
    status: str
    retry_count: int
    created_at: datetime
    started_at: datetime | None
    last_event_at: datetime | None
    user_id: uuid.UUID | None = None
    cancel_requested: bool = False


@dataclass
class SweepPlan:
    start: list[InterruptedTask] = field(default_factory=list)  # 还没开始过，留在 pending 等认领
    rerun: list[InterruptedTask] = field(default_factory=list)  # 中途被打断，清掉事件从头重跑
    fail: list[InterruptedTask] = field(default_factory=list)  # 太久以前的或已经重跑过一次的
    cancel: list[InterruptedTask] = field(default_factory=list)  # 用户已经点过停止，不再重跑


def plan_sweep(tasks: Sequence[InterruptedTask], *, now: datetime, window: timedelta) -> SweepPlan:
    """按最近活动时间决定每个中断任务怎么收尾。纯函数，便于单测。"""
    plan = SweepPlan()
    for item in tasks:
        if item.cancel_requested:
            plan.cancel.append(item)
            continue
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
    def __init__(self, session_factory: Callable[[], AsyncSession] | None = None, worker_id: str | None = None) -> None:
        self._session_factory = session_factory
        self.worker_id = worker_id or new_worker_id()
        # 本进程认领并正在跑的任务。认领和登记之间没有 await，所以两者总是一致的。
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._claimed: set[uuid.UUID] = set()
        self._cancel_requested: set[uuid.UUID] = set()
        self._early_cancels: dict[uuid.UUID, float] = {}
        # 被别的 worker 接手的任务：协程取消后不再写库。
        self._lost: set[uuid.UUID] = set()
        self._signals: dict[uuid.UUID, BatchSignal] = {}
        self._shutting_down = False
        self._draining = False
        self._bus: GenerationBus | None = None
        self._background: set[asyncio.Task[None]] = set()
        self._claimer: asyncio.Task[None] | None = None
        self._claim_again = False
        self._claim_lock: asyncio.Lock | None = None
        self._slot_notice_pending = False

    # ---- 会话 ----

    def _session(self) -> AsyncSession:
        if self._session_factory is None:
            from app.core.database import AsyncSessionLocal

            self._session_factory = AsyncSessionLocal
        return self._session_factory()

    def accepting(self) -> bool:
        return not self._shutting_down and not self._draining

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
        """批次有新事件：唤醒本进程的订阅者；接了通知总线时，再通知其他进程。"""
        self.notify_local(key)
        if self._bus is not None:
            self._bus.send_batch_soon(key)

    def notify_local(self, key: uuid.UUID) -> None:
        signal = self._signals.get(key)
        if signal is not None:
            signal.notify()

    def wake_all(self) -> None:
        """监听连接刚（重新）连上或断开：让所有订阅者都重读一次库。"""
        for signal in list(self._signals.values()):
            signal.notify()

    def wake_is_live(self) -> bool:
        """订阅者能否指望被及时唤醒。web 进程的监听连接断开时，事件流要改为定时读库。"""
        return self._bus is None or self._bus.healthy

    def wakes_are_merged(self) -> bool:
        """接了通知总线时，唤醒来自其他进程合并过的批次通知，订阅者被唤醒后不用再等一会儿攒事件。"""
        return self._bus is not None

    def subscriber_count(self) -> int:
        return sum(signal.refs for signal in self._signals.values())

    # ---- 跨进程 ----

    def attach_bus(self, bus: GenerationBus | None) -> None:
        self._bus = bus

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def handle_dispatch_notice(self, _payload: str) -> None:
        """有新批次，或别的 worker 刚空出名额：认领一轮。"""
        self.kick()

    def handle_cancel_notice(self, payload: str) -> None:
        task_id = _parse_uuid(payload)
        if task_id is None:
            return
        if task_id in self._claimed:
            self._spawn(self.cancel(task_id))
            return
        # 不在本进程：可能是别的 worker 的，也可能是本进程刚认领、还没登记。
        self._early_cancels[task_id] = asyncio.get_running_loop().time()
        self._prune_early_cancels()

    def handle_batch_notice(self, payload: str) -> None:
        key = _parse_uuid(payload)
        if key is not None:
            self.notify_local(key)

    def _prune_early_cancels(self) -> None:
        cutoff = asyncio.get_running_loop().time() - EARLY_CANCEL_TTL_SECONDS
        for task_id, seen in list(self._early_cancels.items()):
            if seen < cutoff:
                self._early_cancels.pop(task_id, None)

    def _announce_free_slot(self) -> None:
        """名额空出来了：本进程马上补领；其他 worker 靠派发通知补领（合并同一时刻的多次）。"""
        self.kick()
        if self._bus is None or self._slot_notice_pending:
            return
        self._slot_notice_pending = True

        async def send() -> None:
            await asyncio.sleep(0.05)
            self._slot_notice_pending = False
            await self._bus.send(CHANNEL_DISPATCH, "slot")

        self._spawn(send())

    # ---- 认领 ----

    def kick(self) -> None:
        """尽快认领一轮。正在认领时只记一下，这一轮结束后再来一轮。"""
        if not self.accepting():
            return
        if self._claimer is not None and not self._claimer.done():
            self._claim_again = True
            return
        self._claimer = asyncio.ensure_future(self._claim_until_settled())

    def idle(self) -> bool:
        """没有在跑的任务，也没有待做的认领。测试和排空用。"""
        return not self._tasks and (self._claimer is None or self._claimer.done())

    async def _claim_until_settled(self) -> None:
        while True:
            self._claim_again = False
            try:
                await self.claim_once()
            except Exception as exc:  # noqa: BLE001 - 认领失败等下一次唤醒或定时扫描
                print(f"[generation] 认领失败：{exc!r}", flush=True)
            if not self._claim_again or not self.accepting():
                return

    async def claim_once(self) -> int:
        """按先来后到认领能拿到名额的 pending 任务交给本进程跑，返回认领的个数。

        拿不到名额的写一条 queued 事件（每种原因一次）。所有 worker 共用同一把咨询锁，认领排着做。
        """
        if not self.accepting():
            return 0
        if self._claim_lock is None:
            self._claim_lock = asyncio.Lock()
        async with self._claim_lock:
            return await self._claim_locked()

    async def _claim_locked(self) -> int:
        settings = get_settings()
        global_limit = max(0, settings.generation_max_concurrency)
        touched: set[uuid.UUID] = set()
        claimed: list[tuple[uuid.UUID, uuid.UUID]] = []
        async with self._session() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CLAIM_LOCK_KEY})
            ranked = (
                select(
                    GenerationTask.id,
                    GenerationTask.batch_id,
                    GenerationTask.requested_by_user_id,
                    GenerationTask.created_at,
                    func.row_number()
                    .over(
                        partition_by=GenerationTask.requested_by_user_id,
                        order_by=(GenerationTask.created_at, GenerationTask.id),
                    )
                    .label("rank"),
                )
                .where(GenerationTask.status == "pending", GenerationTask.cancel_requested_at.is_(None))
                .subquery()
            )
            candidates = (
                await session.execute(
                    select(ranked.c.id, ranked.c.batch_id, ranked.c.requested_by_user_id)
                    .where(ranked.c.rank <= CLAIM_PER_USER_WINDOW)
                    .order_by(ranked.c.created_at, ranked.c.id)
                    .limit(CLAIM_SCAN_LIMIT)
                )
            ).all()
            if not candidates:
                await session.commit()
                return 0

            users = {row.requested_by_user_id for row in candidates}
            limits = await _load_user_limits(session, users)
            running_total = int(
                await session.scalar(select(func.count()).select_from(GenerationTask).where(GenerationTask.status == "running"))
                or 0
            )
            running_by_user: dict[uuid.UUID, int] = {
                user_id: int(count)
                for user_id, count in (
                    await session.execute(
                        select(GenerationTask.requested_by_user_id, func.count())
                        .where(GenerationTask.status == "running", GenerationTask.requested_by_user_id.in_(users))
                        .group_by(GenerationTask.requested_by_user_id)
                    )
                ).all()
            }

            free = global_limit - running_total
            chosen: list[Any] = []
            waiting: list[tuple[Any, str, str]] = []
            for row in candidates:
                if row.id in self._claimed:
                    # 被接手退回 pending、但本地旧协程还没停下的：等它停了再说。
                    continue
                user_id = row.requested_by_user_id
                limit = limits[user_id]
                if running_by_user.get(user_id, 0) >= limit:
                    waiting.append((row, "user_limit", user_queued_message(limit)))
                elif free <= 0:
                    waiting.append((row, "global_limit", GLOBAL_QUEUED_MESSAGE))
                else:
                    chosen.append(row)
                    free -= 1
                    running_by_user[user_id] = running_by_user.get(user_id, 0) + 1

            if chosen:
                # 停止接口对 pending 任务加行锁后写终态；这里的条件在行锁释放后重新判断，被停掉的不会被认领。
                result = await session.execute(
                    update(GenerationTask)
                    .where(
                        GenerationTask.id.in_([row.id for row in chosen]),
                        GenerationTask.status == "pending",
                        GenerationTask.cancel_requested_at.is_(None),
                    )
                    .values(status="running", started_at=func.now(), worker_id=self.worker_id, heartbeat_at=func.now())
                    .returning(GenerationTask.id)
                    .execution_options(synchronize_session=False)
                )
                claimed_ids = set(result.scalars())
                claimed = [(row.id, _signal_key(row.id, row.batch_id)) for row in chosen if row.id in claimed_ids]
                if claimed_ids:
                    were_queued = set(
                        (
                            await session.execute(
                                select(GenerationEvent.task_id)
                                .where(GenerationEvent.task_id.in_(claimed_ids), GenerationEvent.event_type == "queued")
                                .distinct()
                            )
                        ).scalars()
                    )
                    for task_id in were_queued:
                        session.add(GenerationEvent(task_id=task_id, event_type="started", payload={"type": "started"}))

            if waiting:
                existing = {
                    (task_id, reason)
                    for task_id, reason in (
                        await session.execute(
                            select(GenerationEvent.task_id, GenerationEvent.payload["reason"].astext).where(
                                GenerationEvent.task_id.in_([row.id for row, _reason, _message in waiting]),
                                GenerationEvent.event_type == "queued",
                            )
                        )
                    ).all()
                }
                for row, reason, message in waiting:
                    if (row.id, reason) in existing:
                        continue
                    session.add(
                        GenerationEvent(
                            task_id=row.id,
                            event_type="queued",
                            payload={"type": "queued", "reason": reason, "message": message},
                        )
                    )
                    touched.add(_signal_key(row.id, row.batch_id))
            await session.commit()

        for task_id, key in claimed:
            self._register(task_id, key)
        for key in touched:
            self.notify(key)
        return len(claimed)

    def _register(self, task_id: uuid.UUID, key: uuid.UUID) -> None:
        self._claimed.add(task_id)
        if self._early_cancels.pop(task_id, None) is not None:
            self._cancel_requested.add(task_id)
        running = asyncio.create_task(self._drive(task_id, key), name=f"generation-{task_id}")
        self._tasks[task_id] = running
        # 清理放在 done 回调里：开跑前就被取消的协程不会执行自己的 finally。
        running.add_done_callback(lambda _done, task_id=task_id, key=key: self._forget(task_id, key))

    def _forget(self, task_id: uuid.UUID, key: uuid.UUID) -> None:
        self._tasks.pop(task_id, None)
        self._claimed.discard(task_id)
        self._cancel_requested.discard(task_id)
        self._lost.discard(task_id)
        self.notify(key)
        if not self._shutting_down:
            self._announce_free_slot()

    # ---- 定时任务 ----

    async def scan_once(self) -> int:
        """兜底：接手失联任务、补认领没收到派发通知的任务、处理没收到停止通知的停止请求。返回新认领的个数。"""
        if self._shutting_down:
            return 0
        await self.sweep_stale()
        claimed = [task_id for task_id in self._claimed if task_id not in self._cancel_requested]
        if claimed:
            async with self._session() as session:
                flagged = list(
                    (
                        await session.execute(
                            select(GenerationTask.id).where(
                                GenerationTask.id.in_(claimed), GenerationTask.cancel_requested_at.is_not(None)
                            )
                        )
                    ).scalars()
                )
            for task_id in flagged:
                await self.cancel(task_id)
        started = await self.claim_once()
        if started:
            print(f"[generation] 扫描认领 {started} 个任务", flush=True)
        return started

    async def heartbeat_once(self) -> None:
        """给手上的任务续期。续不上的（库里已归别的 worker，或被扫尾退回了 pending）说明被接手了，停掉本地协程、不写库。"""
        ids = list(self._claimed)
        if not ids:
            return
        async with self._session() as session:
            alive = set(
                (
                    await session.execute(
                        update(GenerationTask)
                        .where(
                            GenerationTask.id.in_(ids),
                            GenerationTask.status == "running",
                            GenerationTask.worker_id == self.worker_id,
                        )
                        .values(heartbeat_at=func.now())
                        .returning(GenerationTask.id)
                        .execution_options(synchronize_session=False)
                    )
                ).scalars()
            )
            missing = [task_id for task_id in ids if task_id not in alive]
            lost: list[uuid.UUID] = []
            if missing:
                rows = (
                    await session.execute(
                        select(GenerationTask.id, GenerationTask.status, GenerationTask.worker_id).where(
                            GenerationTask.id.in_(missing)
                        )
                    )
                ).all()
                # 已经写到终态的是本进程正在收尾，不算被接手。
                lost = [
                    row.id
                    for row in rows
                    if row.status not in TERMINAL_STATUSES and (row.status == "pending" or row.worker_id != self.worker_id)
                ]
            await session.commit()
        for task_id in lost:
            running = self._tasks.get(task_id)
            if running is not None and not running.done():
                print(f"[generation] 任务已被别的 worker 接手，停止本地执行 task={task_id}", flush=True)
                self._lost.add(task_id)
                running.cancel()

    async def sweep_stale(self, *, startup: bool = False) -> SweepPlan | None:
        """接手失联任务：running 但超过续期时限没续期的。启动时再顺带处理排队太久的 pending。

        同一时刻只有一个 worker 做（拿不到锁就跳过，返回 None）。本进程手上的任务不碰。
        """
        settings = get_settings()
        window = timedelta(minutes=max(0, settings.generation_resume_window_minutes))
        lease = timedelta(seconds=max(1.0, settings.generation_worker_lease_seconds))
        # 收尾过程中会多次提交，锁放在另一个会话的事务里，从头持有到尾。
        async with self._session() as lock_session, self._session() as session:
            locked = await lock_session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": SWEEP_LOCK_KEY})
            if not locked:
                await lock_session.commit()
                return None
            plan = await self._sweep_locked(session, startup=startup, window=window, lease=lease)
            await lock_session.commit()
        touched = plan.rerun or plan.fail or plan.cancel
        if touched or startup:
            print(
                f"[generation] 扫尾（{'启动' if startup else '失联接手'}）：重跑 {len(plan.rerun)}，标失败 {len(plan.fail)}，"
                f"按停止请求收尾 {len(plan.cancel)}，排队中 {len(plan.start)}",
                flush=True,
            )
        for item in [*plan.rerun, *plan.fail, *plan.cancel]:
            self.notify(_signal_key(item.task_id, item.batch_id))
        if plan.rerun or plan.start:
            self.kick()
        return plan

    async def _sweep_locked(
        self, session: AsyncSession, *, startup: bool, window: timedelta, lease: timedelta
    ) -> SweepPlan:
        now = datetime.now(UTC)
        await _reset_stale_parses(session, now - lease)
        items = await _load_unfinished(session, stale_before=now - lease, include_pending=startup, exclude=self._claimed)
        plan = plan_sweep(items, now=now, window=window)
        await _reset_for_rerun(session, plan.rerun)
        service = GenerationService(session)
        for item in plan.fail:
            await service.finish_unfinished_task(
                item.task_id,
                status="failed",
                message=INTERRUPTED_ERROR_MESSAGE,
                user_message=INTERRUPTED_USER_MESSAGE,
            )
        for item in plan.cancel:
            await service.finish_unfinished_task(
                item.task_id,
                status="cancelled",
                message=CANCELLED_ERROR_MESSAGE,
                user_message=CANCELLED_USER_MESSAGE,
            )
        for batch_id in {item.batch_id for item in plan.rerun}:
            await service.recompute_batch_status(batch_id)
        if startup:
            await _recompute_settled_batches(session, service)
        await _discard_orphan_staging(session)
        await session.commit()
        return plan

    async def startup_sweep(self) -> SweepPlan:
        """worker 启动时调用：接手失联任务、处理排队太久的任务、重算停在进行中的批次。"""
        plan = await self.sweep_stale(startup=True)
        return plan if plan is not None else SweepPlan()

    async def run_scan_loop(self, interval: float, wake: asyncio.Event | None = None) -> None:
        """worker 的兜底循环：每隔 interval 秒扫一次；wake 被置位时（例如监听刚重连）立刻扫。"""
        while not self._shutting_down:
            try:
                await self.scan_once()
            except Exception as exc:  # noqa: BLE001 - 扫描失败下一轮再来
                print(f"[generation] 兜底扫描失败：{exc!r}", flush=True)
            if wake is None:
                await asyncio.sleep(interval)
                continue
            try:
                await asyncio.wait_for(wake.wait(), timeout=interval)
            except TimeoutError:
                pass
            wake.clear()

    async def run_heartbeat_loop(self, interval: float) -> None:
        while not self._shutting_down:
            await asyncio.sleep(interval)
            try:
                await self.heartbeat_once()
            except Exception as exc:  # noqa: BLE001 - 续期失败下一轮再来；连续失败到时限才会被接手
                print(f"[generation] 续期失败：{exc!r}", flush=True)

    # ---- 停止与退出 ----

    def is_active(self, task_id: uuid.UUID) -> bool:
        return task_id in self._tasks

    async def cancel(self, task_id: uuid.UUID) -> bool:
        """请求停止本进程正在跑的任务，返回 True。不在本进程跑的返回 False，调用方自己处理。"""
        running = self._tasks.get(task_id)
        if running is None or running.done():
            return False
        self._cancel_requested.add(task_id)
        self._schedule_hard_cancel(task_id)
        return True

    def _schedule_hard_cancel(self, task_id: uuid.UUID) -> None:
        running = self._tasks.get(task_id)
        if running is not None and not running.done():
            asyncio.get_running_loop().call_later(HARD_CANCEL_GRACE_SECONDS, self._hard_cancel, task_id, running)

    def _hard_cancel(self, task_id: uuid.UUID, running: asyncio.Task[None]) -> None:
        if not running.done() and task_id in self._cancel_requested:
            running.cancel()

    async def drain(self, timeout: float, *, busy: Callable[[], bool] | None = None) -> bool:
        """不再认领新任务，等手上的跑完。停止请求和续期照常处理。返回是否在时限内排空。

        busy 用来再等一些别的活（例如本进程在做的批次解析）。
        """
        self._draining = True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        if self._claimer is not None and not self._claimer.done():
            await asyncio.wait({self._claimer}, timeout=max(0.0, deadline - loop.time()))
        last_report = 0.0
        while self._tasks or (busy is not None and busy()):
            now = loop.time()
            if now >= deadline:
                return False
            if now - last_report >= 30:
                print(f"[generation] 排空中：还有 {len(self._tasks)} 个任务在跑", flush=True)
                last_report = now
            await asyncio.sleep(0.5)
        return True

    async def shutdown(self) -> None:
        """进程退出：取消剩下的协程但不改任务状态，并把它们的续期作废，让别的 worker 马上接手。"""
        self._shutting_down = True
        for pending in list(self._background):
            pending.cancel()
        if self._claimer is not None:
            self._claimer.cancel()
        remaining = [task_id for task_id, task in self._tasks.items() if not task.done()]
        running = [self._tasks[task_id] for task_id in remaining]
        for task in running:
            task.cancel()
        if running:
            await asyncio.wait(running, timeout=3)
        if remaining:
            try:
                async with self._session() as session:
                    await session.execute(
                        update(GenerationTask)
                        .where(
                            GenerationTask.id.in_(remaining),
                            GenerationTask.status == "running",
                            GenerationTask.worker_id == self.worker_id,
                        )
                        .values(heartbeat_at=EXPIRED_LEASE)
                        .execution_options(synchronize_session=False)
                    )
                    await session.commit()
                print(f"[generation] 退出时交出 {len(remaining)} 个未跑完的任务", flush=True)
            except Exception as exc:  # noqa: BLE001 - 写不进去就等续期自然超时
                print(f"[generation] 交出未跑完的任务失败：{exc!r}", flush=True)

    async def _drive(self, task_id: uuid.UUID, key: uuid.UUID) -> None:
        try:
            async with self._session() as session:
                service = GenerationService(session, cancel_check=lambda: task_id in self._cancel_requested)
                task = await session.get(GenerationTask, task_id, populate_existing=True)
                if task is None or task.status != "running" or task.worker_id != self.worker_id:
                    return
                if task_id in self._cancel_requested:
                    # 认领前已收到停止：run_task 第一步就会按停止收尾，模型卡住时照常到点强停。
                    self._schedule_hard_cancel(task_id)
                self.notify(key)
                async for _event in service.run_task(task):
                    self.notify(key)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底收尾在 finally 里
            print(f"[generation] 任务执行异常 task={task_id}: {exc!r}", flush=True)
        finally:
            # 进程退出、或任务已被别的 worker 接手时不写库。
            if not self._shutting_down and task_id not in self._lost:
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

    def stats(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "running": len(self._tasks),
            "draining": int(self._draining),
            "subscribers": self.subscriber_count(),
        }


async def _load_unfinished(
    session: AsyncSession,
    *,
    stale_before: datetime,
    include_pending: bool,
    exclude: set[uuid.UUID],
) -> list[InterruptedTask]:
    last_event_at = (
        select(func.max(GenerationEvent.created_at))
        .where(GenerationEvent.task_id == GenerationTask.id)
        .correlate(GenerationTask)
        .scalar_subquery()
    )
    stale_running = (GenerationTask.status == "running") & (
        func.coalesce(GenerationTask.heartbeat_at, GenerationTask.started_at, GenerationTask.created_at) < stale_before
    )
    condition = or_(stale_running, GenerationTask.status == "pending") if include_pending else stale_running
    query = select(
        GenerationTask.id,
        GenerationTask.batch_id,
        GenerationTask.status,
        GenerationTask.retry_count,
        GenerationTask.created_at,
        GenerationTask.started_at,
        GenerationTask.requested_by_user_id,
        GenerationTask.cancel_requested_at,
        last_event_at.label("last_event_at"),
    ).where(condition)
    if exclude:
        query = query.where(GenerationTask.id.not_in(exclude))
    result = await session.execute(query)
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
            cancel_requested=row.cancel_requested_at is not None,
        )
        for row in result.all()
    ]


async def _recompute_settled_batches(session: AsyncSession, service: GenerationService) -> None:
    """任务都已结束、批次却还停在进行中：上个进程在最后一个任务写完终态、还没重算批次时退出了。"""
    live = select(GenerationTask.id).where(
        GenerationTask.batch_id == GenerationBatch.id, GenerationTask.status.in_(("pending", "running"))
    )
    any_task = select(GenerationTask.id).where(GenerationTask.batch_id == GenerationBatch.id)
    stale = list(
        (
            await session.execute(
                select(GenerationBatch.id).where(
                    GenerationBatch.status.in_(("pending", "running")), ~live.exists(), any_task.exists()
                )
            )
        ).scalars()
    )
    for batch_id in stale:
        await service.recompute_batch_status(batch_id)
    if stale:
        print(f"[generation] 扫尾：重算 {len(stale)} 个停在进行中的批次", flush=True)


def _parse_uuid(payload: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(payload).strip())
    except ValueError:
        return None


async def _load_user_limits(session: AsyncSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
    limits = {user_id: _resolve_user_limit(None) for user_id in user_ids}
    if user_ids:
        result = await session.execute(select(User.id, User.generation_concurrency).where(User.id.in_(user_ids)))
        for user_id, override in result.all():
            limits[user_id] = _resolve_user_limit(override)
    return limits


async def _reset_stale_parses(session: AsyncSession, stale_before: datetime) -> None:
    """解析到一半、做解析的 worker 不在了：原件还在暂存区，状态退回 pending，等着的任务会重新解析。"""
    await session.execute(
        update(GenerationBatch)
        .where(
            GenerationBatch.parse_status == "running",
            or_(GenerationBatch.parse_heartbeat_at.is_(None), GenerationBatch.parse_heartbeat_at < stale_before),
        )
        .values(parse_status="pending", parse_worker_id=None)
        .execution_options(synchronize_session=False)
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
        task.worker_id = None
        task.heartbeat_at = None
        page = await session.get(Page, task.page_id)
        if page is not None and page.deleted_at is None:
            page.status = "generating"
        payload: dict[str, Any] = {"type": "restarted", "message": RESTARTED_USER_MESSAGE, "model_key": task.model_key}
        session.add(GenerationEvent(task_id=task.id, event_type="restarted", payload=payload))
    await session.commit()


generation_runner = GenerationRunner()
