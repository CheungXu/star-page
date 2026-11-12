"""web / worker 分进程运行的集成测试：派发、兜底扫描、两种停止、停止与认领的竞争、扫尾、跨进程唤醒与轮询降级，
以及多个 worker：共用名额、退出交接、失联接手、被接手后停手、排空。

同一个测试进程里分别构造「web 侧」和「worker 侧」的执行器对象，用真实的 LISTEN/NOTIFY 通信。
模型、上传、计费、技能路由沿用 test_generation_runner_db 里的假实现。需要一个跑过迁移的空库：

    TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/stars_page_test \
        .venv/bin/python -m pytest tests/test_web_worker_split_db.py

未设置 TEST_DATABASE_URL 时跳过。不要指向线上库：启动扫尾会改所有未结束任务。
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="未设置 TEST_DATABASE_URL")

if TEST_DATABASE_URL:
    from sqlalchemy import func, select, update  # noqa: E402
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

    from app.models.entities import GenerationBatch, GenerationEvent, GenerationTask, User  # noqa: E402
    from app.services import generation_runner as runner_module  # noqa: E402
    from app.services import generation_service as service_module  # noqa: E402
    from app.services.billing.account import BillingService  # noqa: E402
    from app.services.generation_bus import CHANNEL_BATCH, CHANNEL_CANCEL, CHANNEL_DISPATCH, GenerationBus, send  # noqa: E402
    from app.services.generation_events import HEARTBEAT, stream_task_events  # noqa: E402
    from app.services.generation_runner import GenerationRunner  # noqa: E402
    from app.services.generation_service import GenerationService  # noqa: E402
    from test_generation_runner_db import (  # noqa: E402
        FakeClient,
        _create_batch,
        _fake_reserve,
        _fake_upload,
        _no_skill,
        _noop,
        _settle_leftovers,
        _task,
        _wait_until,
    )

CANCEL_MESSAGE = "用户停止了生成"
CANCEL_USER_MESSAGE = "已停止"


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    monkeypatch.setattr(BillingService, "ensure_can_start_batch", _noop)
    monkeypatch.setattr(BillingService, "reserve_for_batch", _fake_reserve)
    monkeypatch.setattr(BillingService, "record_anon_batch_started", _noop)
    monkeypatch.setattr(GenerationService, "_resolve_model_keys", lambda self, keys, allowed_keys=None: list(keys))
    monkeypatch.setattr(GenerationService, "_resolve_task_skill_key", _no_skill)
    monkeypatch.setattr(GenerationService, "_upload_page_html", _fake_upload)
    monkeypatch.setattr(GenerationService, "_settle_generation_billing", _noop)
    monkeypatch.setattr(service_module, "create_llm_client", lambda model_key: FakeClient(model_key))
    monkeypatch.setattr(runner_module, "HARD_CANCEL_GRACE_SECONDS", 0.2)
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "generation_reasoning_flush_seconds", 0.05)
    monkeypatch.setattr(settings, "generation_progress_flush_seconds", 0.05)


def _run(flow) -> None:
    async def wrapper() -> None:
        engine = create_async_engine(TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1))
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        worker = GenerationRunner(session_factory=factory)
        web = GenerationRunner(session_factory=factory)
        buses: list[GenerationBus] = []
        await _settle_leftovers(factory)

        def make_bus(**kwargs) -> GenerationBus:
            bus = GenerationBus(dsn=TEST_DATABASE_URL, session_factory=factory, **kwargs)
            buses.append(bus)
            return bus

        try:
            await flow(factory, worker, web, make_bus)
        finally:
            await worker.shutdown()
            await web.shutdown()
            for bus in buses:
                await bus.stop()
            await _settle_leftovers(factory)
            await engine.dispose()

    asyncio.run(wrapper())


async def _owner(factory, creation):
    return (await _task(factory, creation.runs[0].task_id)).requested_by_user_id


async def _set_user_limit(factory, creation, limit: int) -> None:
    async with factory() as session:
        await session.execute(update(User).where(User.id == await _owner(factory, creation)).values(generation_concurrency=limit))
        await session.commit()


async def _wait_status(factory, task_id, statuses: set[str], timeout: float = 10.0) -> None:
    async def done() -> bool:
        return (await _task(factory, task_id)).status in statuses

    await _wait_until(done, timeout)


async def _wait_healthy(bus: GenerationBus, value: bool = True, timeout: float = 10.0) -> None:
    async def check() -> bool:
        return bus.healthy is value

    await _wait_until(check, timeout)


async def _count_events(factory, task_id, event_type: str) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(GenerationEvent)
                .where(GenerationEvent.task_id == task_id, GenerationEvent.event_type == event_type)
            )
        )


async def _request_cancel(factory, task_id) -> str:
    async with factory() as session:
        return await GenerationService(session).request_cancel(
            task_id, message=CANCEL_MESSAGE, user_message=CANCEL_USER_MESSAGE
        )


def test_scan_picks_up_pending_tasks_without_dispatch_notice():
    async def flow(factory, worker, _web, _make_bus):
        creation = await _create_batch(factory, ["slow-a", "slow-b"])
        assert await worker.scan_once() >= 2
        for run in creation.runs:
            assert run.task_id in worker._tasks
        # 已经在跑或排着的任务，再扫一次不会重复启动。
        assert await worker.scan_once() == 0
        for run in creation.runs:
            await _wait_status(factory, run.task_id, {"running"})

    _run(flow)


def test_dispatch_notice_runs_batch_and_wakes_stream_on_other_process():
    async def flow(factory, worker, web, make_bus):
        worker_bus = make_bus()
        worker_bus.subscribe(CHANNEL_DISPATCH, worker.handle_dispatch_notice)
        worker.attach_bus(worker_bus)
        web_bus = make_bus()
        web_bus.subscribe(CHANNEL_BATCH, web.handle_batch_notice)
        web.attach_bus(web_bus)
        await worker_bus.start()
        await web_bus.start()
        await _wait_healthy(worker_bus)
        await _wait_healthy(web_bus)

        creation = await _create_batch(factory, ["ok"])
        task_ids = [creation.runs[0].task_id]

        async def consume():
            # 心跳和轮询都设成一分钟：只有跨进程通知能让这条流及时结束。
            return [
                item
                async for item in stream_task_events(
                    session_factory=factory,
                    runner=web,
                    task_ids=task_ids,
                    signal_key=creation.batch_id,
                    heartbeat_seconds=60,
                    poll_seconds=60,
                    debounce_seconds=0.01,
                )
            ]

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.2)
        await send(CHANNEL_DISPATCH, str(creation.batch_id), factory)
        received = await asyncio.wait_for(consumer, timeout=10)
        events = [item.event for item in received if item is not HEARTBEAT]
        assert "completed" in events and events[-1] == "batch_done"
        assert (await _task(factory, task_ids[0])).status == "succeeded"

    _run(flow)


def test_cancel_pending_and_running_from_web():
    async def flow(factory, worker, _web, _make_bus):
        creation = await _create_batch(factory, ["slow-a", "slow-b"])
        first, second = (run.task_id for run in creation.runs)
        # 个人上限 1：第一个在跑，第二个排队，库里仍是 pending，没有任何 worker 认领。
        await _set_user_limit(factory, creation, 1)
        worker.kick()
        await _wait_status(factory, first, {"running"})
        assert (await _task(factory, second)).status == "pending"
        assert second not in worker._tasks

        # 排队中的：web 直接写成已停止，之后再也不会被认领。
        assert await _request_cancel(factory, second) == "cancelled"
        worker.handle_cancel_notice(str(second))
        await worker.scan_once()
        assert second not in worker._tasks
        assert (await _task(factory, second)).status == "cancelled"
        assert await _count_events(factory, second, "cancelled") == 1

        # 在跑的：web 只记停止请求，不写终态；worker 没收到通知时靠扫描发现并停下。
        assert await _request_cancel(factory, first) == "flagged"
        flagged = await _task(factory, first)
        assert flagged.status == "running" and flagged.cancel_requested_at is not None
        await worker.scan_once()
        await _wait_status(factory, first, {"cancelled"})
        assert await _count_events(factory, first, "cancelled") == 1
        # 已结束的再停一次什么都不做。
        assert await _request_cancel(factory, first) == "noop"

    _run(flow)


async def _async_bool(value: bool) -> bool:
    return value


def test_cancel_and_claim_race_has_single_writer():
    async def flow(factory, _worker, _web, _make_bus):
        for _ in range(8):
            creation = await _create_batch(factory, ["ok"])
            task_id = creation.runs[0].task_id

            async def claim():
                async with factory() as session:
                    return await GenerationService(session).claim_task(task_id)

            claimed, outcome = await asyncio.gather(claim(), _request_cancel(factory, task_id))
            task = await _task(factory, task_id)
            if claimed is None:
                # 停止先锁住：认领落空，终态由停止写。
                assert outcome == "cancelled" and task.status == "cancelled"
            else:
                # 认领先提交：停止只记请求，终态留给 worker。
                assert outcome == "flagged" and task.status == "running" and task.cancel_requested_at is not None
            assert await _count_events(factory, task_id, "cancelled") == (1 if claimed is None else 0)
            if claimed is not None:
                async with factory() as session:
                    await GenerationService(session).finish_unfinished_task(
                        task_id, status="cancelled", message=CANCEL_MESSAGE, user_message=CANCEL_USER_MESSAGE
                    )

    _run(flow)


def test_cancel_notice_before_claim_registered_stops_after_claim():
    async def flow(factory, worker, _web, _make_bus):
        creation = await _create_batch(factory, ["slow"])
        task_id = creation.runs[0].task_id
        # 模拟：停止通知比本进程登记认领先到（认领已在库里提交、还没登记）。通知先记下，登记时补上。
        worker.handle_cancel_notice(str(task_id))
        assert task_id in worker._early_cancels
        assert await worker.claim_once() == 1
        assert task_id in worker._cancel_requested and task_id not in worker._early_cancels
        await _wait_status(factory, task_id, {"cancelled"})

    _run(flow)


def test_startup_sweep_cancels_flagged_and_settles_stale_batch():
    async def flow(factory, worker, _web, _make_bus):
        flagged = await _create_batch(factory, ["ok"])
        flagged_id = flagged.runs[0].task_id
        async with factory() as session:
            assert await GenerationService(session).claim_task(flagged_id) is not None
        assert await _request_cancel(factory, flagged_id) == "flagged"
        # 刚认领的可能还在别的 worker 上跑，不会被扫尾；认领它的 worker 早已不在（续期过期）才接手。
        assert flagged_id not in {item.task_id for item in (await worker.startup_sweep()).cancel}
        async with factory() as session:
            await session.execute(
                update(GenerationTask)
                .where(GenerationTask.id == flagged_id)
                .values(started_at=datetime.now(UTC) - timedelta(minutes=5))
            )
            await session.commit()

        stale = await _create_batch(factory, ["ok"])
        stale_id = stale.runs[0].task_id
        async with factory() as session:
            await session.execute(
                update(GenerationTask).where(GenerationTask.id == stale_id).values(status="succeeded", finished_at=datetime.now(UTC))
            )
            await session.execute(update(GenerationBatch).where(GenerationBatch.id == stale.batch_id).values(status="running"))
            await session.commit()

        plan = await worker.startup_sweep()
        assert flagged_id in {item.task_id for item in plan.cancel}
        task = await _task(factory, flagged_id)
        assert task.status == "cancelled" and task.retry_count == 0
        assert await _count_events(factory, flagged_id, "restarted") == 0
        async with factory() as session:
            assert (await session.get(GenerationBatch, stale.batch_id)).status == "succeeded"

    _run(flow)


def test_stream_polls_while_listener_is_down_and_bus_reconnects():
    async def flow(factory, worker, web, make_bus):
        # web 的监听连接还没连上：healthy 为 False，事件流应当改为定时读库。
        idle_bus = make_bus()
        web.attach_bus(idle_bus)
        assert web.wake_is_live() is False

        creation = await _create_batch(factory, ["ok"])
        task_ids = [creation.runs[0].task_id]

        async def consume():
            return [
                item
                async for item in stream_task_events(
                    session_factory=factory,
                    runner=web,
                    task_ids=task_ids,
                    signal_key=creation.batch_id,
                    heartbeat_seconds=60,
                    poll_seconds=0.2,
                    debounce_seconds=0.01,
                )
            ]

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.2)
        worker.kick()
        received = await asyncio.wait_for(consumer, timeout=10)
        assert [item.event for item in received if item is not HEARTBEAT][-1] == "batch_done"

        # 监听连接被中途掐断：探活发现后断开重连，断开时回调一次（让等待中的事件流改为读库），重连后回调里补读。
        connects: list[int] = []
        disconnects: list[bool] = []
        bus = make_bus(ping_seconds=0.2, ping_timeout_seconds=1)
        bus.subscribe(CHANNEL_CANCEL, lambda _payload: None)
        bus.on_connect(lambda: connects.append(1))
        bus.on_disconnect(lambda: disconnects.append(bus.healthy))
        await bus.start()
        await _wait_healthy(bus)
        await bus.drop_connection()
        await _wait_healthy(bus, False)
        await _wait_healthy(bus, True)
        assert len(connects) == 2
        assert disconnects == [False]

    _run(flow)


def test_listener_drop_wakes_waiting_stream_into_polling():
    async def flow(factory, worker, web, make_bus):
        bus = make_bus(ping_seconds=0.2, ping_timeout_seconds=1)
        bus.subscribe(CHANNEL_BATCH, web.handle_batch_notice)
        bus.on_disconnect(web.wake_all)
        web.attach_bus(bus)
        await bus.start()
        await _wait_healthy(bus)

        creation = await _create_batch(factory, ["ok"])
        task_ids = [creation.runs[0].task_id]

        async def consume():
            # 心跳一分钟：断线后要靠断开回调唤醒，才能在 2 秒轮询里看到 worker 写入的事件。
            return [
                item
                async for item in stream_task_events(
                    session_factory=factory,
                    runner=web,
                    task_ids=task_ids,
                    signal_key=creation.batch_id,
                    heartbeat_seconds=60,
                    poll_seconds=0.2,
                )
            ]

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        # 事件流已经在按 60 秒心跳等通知。掐断监听并让它重连不上，再让 worker 跑：只有断开回调能让事件流及时改为读库。
        bus._dsn = "postgresql://postgres:wrong@127.0.0.1:1/none"
        await bus.drop_connection()
        await _wait_healthy(bus, False)
        worker.kick()
        received = await asyncio.wait_for(consumer, timeout=10)
        assert [item.event for item in received if item is not HEARTBEAT][-1] == "batch_done"

    _run(flow)


def _run_workers(flow, count: int = 2) -> None:
    """多个 worker 共用一个库：每个都有自己的 worker_id。"""

    async def wrapper() -> None:
        engine = create_async_engine(TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1))
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        workers = [GenerationRunner(session_factory=factory, worker_id=f"test-{index}") for index in range(count)]
        await _settle_leftovers(factory)
        try:
            await flow(factory, workers)
        finally:
            for worker in workers:
                await worker.shutdown()
            await _settle_leftovers(factory)
            await engine.dispose()

    asyncio.run(wrapper())


async def _statuses(factory, ids) -> list[tuple[str, str | None]]:
    rows = [await _task(factory, task_id) for task_id in ids]
    return [(row.status, row.worker_id) for row in rows]


def test_workers_share_global_limit_and_backfill(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "generation_max_concurrency", 2)

    async def flow(factory, workers):
        a, b = workers
        creation = await _create_batch(factory, ["slow-a", "slow-b", "slow-c"])
        ids = [run.task_id for run in creation.runs]
        # 两个 worker 同时认领：全站名额 2，一共只有 2 个开跑，按先后顺序。
        claimed = await asyncio.gather(a.claim_once(), b.claim_once())
        assert sum(claimed) == 2
        states = await _statuses(factory, ids)
        assert [state for state, _owner in states] == ["running", "running", "pending"]
        assert {owner for _state, owner in states[:2]} <= {"test-0", "test-1"}
        assert [item["reason"] for item in await _payloads_of(factory, ids[2])] == ["global_limit"]
        # 名额满时再认领也领不到。
        assert await a.claim_once() == 0 and await b.claim_once() == 0

        # 停掉一个，名额空出来，排队的那个被认领并收到 started。
        holder = a if ids[0] in a._tasks else b
        assert await holder.cancel(ids[0]) is True
        await _wait_status(factory, ids[0], {"cancelled"})
        await _wait_status(factory, ids[2], {"running"})
        assert await _count_events(factory, ids[2], "started") == 1
        for worker in workers:
            for task_id in list(worker._tasks):
                await worker.cancel(task_id)
        await _wait_until(lambda: _async_bool(all(worker.idle() for worker in workers)))

    _run_workers(flow)


async def _payloads_of(factory, task_id) -> list[dict]:
    async with factory() as session:
        result = await session.execute(
            select(GenerationEvent.payload)
            .where(GenerationEvent.task_id == task_id, GenerationEvent.event_type == "queued")
            .order_by(GenerationEvent.sequence)
        )
        return list(result.scalars().all())


def test_shutdown_hands_over_to_other_worker():
    async def flow(factory, workers):
        a, b = workers
        creation = await _create_batch(factory, ["slow"])
        task_id = creation.runs[0].task_id
        assert await a.claim_once() == 1
        await _wait_until(lambda: _count_reasoning(factory, task_id))
        # a 被强制退出：任务留在 running，续期作废；b 的下一次扫描马上接手，从头重跑一次。
        await a.shutdown()
        task = await _task(factory, task_id)
        assert task.status == "running" and task.worker_id == "test-0"
        assert await b.scan_once() == 1
        task = await _task(factory, task_id)
        assert (task.status, task.worker_id, task.retry_count) == ("running", "test-1", 1)
        assert await _count_events(factory, task_id, "restarted") == 1
        await b.cancel(task_id)
        await _wait_status(factory, task_id, {"cancelled"})

    _run_workers(flow)


async def _count_reasoning(factory, task_id) -> bool:
    return await _count_events(factory, task_id, "reasoning_delta") >= 1


def test_live_worker_tasks_are_not_swept():
    async def flow(factory, workers):
        a, b = workers
        creation = await _create_batch(factory, ["slow"])
        task_id = creation.runs[0].task_id
        assert await a.claim_once() == 1
        await a.heartbeat_once()
        # b 启动或扫描时，a 在续期，不碰 a 的任务。
        plan = await b.startup_sweep()
        assert task_id not in {item.task_id for item in [*plan.rerun, *plan.fail, *plan.cancel]}
        assert await b.scan_once() == 0
        assert (await _task(factory, task_id)).worker_id == "test-0"
        await a.cancel(task_id)
        await _wait_status(factory, task_id, {"cancelled"})

    _run_workers(flow)


def test_lost_lease_stops_local_coroutine_without_writing():
    async def flow(factory, workers):
        a, _b = workers
        creation = await _create_batch(factory, ["slow"])
        task_id = creation.runs[0].task_id
        assert await a.claim_once() == 1
        await _wait_until(lambda: _count_reasoning(factory, task_id))
        # 模拟 a 卡住太久、任务已被别的 worker 接手。a 的续期发现后停掉本地协程，不写终态。
        async with factory() as session:
            await session.execute(update(GenerationTask).where(GenerationTask.id == task_id).values(worker_id="someone-else"))
            await session.commit()
        await a.heartbeat_once()
        await _wait_until(lambda: _async_bool(a.idle()))
        task = await _task(factory, task_id)
        assert (task.status, task.worker_id) == ("running", "someone-else")
        assert await _count_events(factory, task_id, "failed") == 0
        assert await _count_events(factory, task_id, "cancelled") == 0

    _run_workers(flow)


def test_drain_finishes_running_and_stops_claiming():
    async def flow(factory, workers):
        a, _b = workers
        first = await _create_batch(factory, ["ok"])
        assert await a.claim_once() == 1
        drain = asyncio.create_task(a.drain(10))
        await asyncio.sleep(0)
        # 排空期间新来的任务不再认领，留给其他 worker。
        second = await _create_batch(factory, ["ok"])
        a.kick()
        assert await a.claim_once() == 0
        assert await asyncio.wait_for(drain, timeout=10) is True
        assert (await _task(factory, first.runs[0].task_id)).status == "succeeded"
        assert (await _task(factory, second.runs[0].task_id)).status == "pending"

    _run_workers(flow)


def test_drain_times_out_when_task_keeps_running():
    async def flow(factory, workers):
        a, _b = workers
        await _create_batch(factory, ["slow"])
        assert await a.claim_once() == 1
        assert await a.drain(0.3) is False
        assert not a.idle()

    _run_workers(flow)


class GateClient:
    """先吐一段思考，然后等测试放行再输出页面。每个实例单独放行，用来控制两个 worker 谁先跑完。"""

    instances: list["GateClient"] = []

    def __init__(self) -> None:
        self.release = asyncio.Event()
        GateClient.instances.append(self)

    async def stream_text(self, _messages):
        from test_generation_runner_db import HTML

        from app.services.llm.types import LlmStreamChunk

        yield LlmStreamChunk(type="reasoning_delta", provider="fake", model="fake", reasoning_text="等放行")
        await self.release.wait()
        yield LlmStreamChunk(type="text_delta", provider="fake", model="fake", text=HTML)


async def _versions(factory, page_id) -> int:
    from app.models.entities import PageVersion

    async with factory() as session:
        return int(await session.scalar(select(func.count(PageVersion.id)).where(PageVersion.page_id == page_id)))


def test_taken_over_worker_cannot_write_back(monkeypatch):
    """A 卡住超过续期时限、B 接手重跑后，A 回过神来跑完，不能把 B 手上的任务写成功、也不能多结一次账。"""
    GateClient.instances = []
    monkeypatch.setattr(
        service_module,
        "create_llm_client",
        lambda model_key: GateClient() if model_key.startswith("gate") else FakeClient(model_key),
    )

    async def flow(factory, workers):
        a, b = workers
        creation = await _create_batch(factory, ["gate"])
        task_id, page_id = creation.runs[0].task_id, creation.runs[0].page_id
        assert await a.claim_once() == 1
        await _wait_until(lambda: _count_reasoning(factory, task_id))

        # A 停止续期（卡住），续期过期；B 扫描时接手，从头重跑。
        async with factory() as session:
            await session.execute(
                update(GenerationTask)
                .where(GenerationTask.id == task_id)
                .values(heartbeat_at=datetime.now(UTC) - timedelta(minutes=5))
            )
            await session.commit()
        assert await b.scan_once() == 1
        await _wait_until(lambda: _async_bool(len(GateClient.instances) == 2))
        task = await _task(factory, task_id)
        assert (task.status, task.worker_id) == ("running", "test-1")

        # A 先跑完：它已经不是这个任务的执行者，什么都不能写。
        GateClient.instances[0].release.set()
        await _wait_until(lambda: _async_bool(task_id not in a._tasks))
        task = await _task(factory, task_id)
        assert (task.status, task.worker_id) == ("running", "test-1")
        assert await _versions(factory, page_id) == 0

        # B 跑完，只有一个版本。
        GateClient.instances[1].release.set()
        await _wait_status(factory, task_id, {"succeeded"})
        assert await _versions(factory, page_id) == 1
        assert await _count_events(factory, task_id, "completed") == 1

    _run_workers(flow)


def test_sweep_skips_task_that_renewed(monkeypatch):
    """扫尾查出过期任务后、动手前执行者又续上了期：不重置、不标失败；兜底收尾也不碰别人的任务。"""
    from app.services.generation_runner import InterruptedTask, _reset_for_rerun

    GateClient.instances = []
    monkeypatch.setattr(
        service_module,
        "create_llm_client",
        lambda model_key: GateClient() if model_key.startswith("gate") else FakeClient(model_key),
    )

    async def flow(factory, workers):
        a, _b = workers
        creation = await _create_batch(factory, ["gate"])
        task_id = creation.runs[0].task_id
        assert await a.claim_once() == 1
        await _wait_until(lambda: _count_reasoning(factory, task_id))
        task = await _task(factory, task_id)
        item = InterruptedTask(
            task_id=task_id,
            batch_id=task.batch_id,
            status="running",
            retry_count=0,
            created_at=task.created_at,
            started_at=task.started_at,
            last_event_at=None,
            user_id=task.requested_by_user_id,
            cancel_requested=False,
        )
        stale_before = datetime.now(UTC) - timedelta(seconds=45)
        async with factory() as session:
            assert await _reset_for_rerun(session, [item], stale_before=stale_before) == []
            service = service_module.GenerationService(session)
            assert not await service.finish_unfinished_task(
                task_id, status="failed", message="x", user_message="x", stale_before=stale_before
            )
            assert not await service.finish_unfinished_task(
                task_id, status="failed", message="x", user_message="x", owner="test-1"
            )
        task = await _task(factory, task_id)
        assert (task.status, task.worker_id, task.retry_count) == ("running", "test-0", 0)
        GateClient.instances[0].release.set()
        await _wait_status(factory, task_id, {"succeeded"})

    _run_workers(flow)
