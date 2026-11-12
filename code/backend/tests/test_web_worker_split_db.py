"""web / worker 分进程运行的集成测试：派发、兜底扫描、两种停止、停止与认领的竞争、扫尾、跨进程唤醒与轮询降级。

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
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="未设置 TEST_DATABASE_URL")

if TEST_DATABASE_URL:
    from sqlalchemy import func, select, update  # noqa: E402
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

    from app.models.entities import GenerationBatch, GenerationEvent, GenerationTask  # noqa: E402
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
            await engine.dispose()

    asyncio.run(wrapper())


async def _owner(factory, creation):
    return (await _task(factory, creation.runs[0].task_id)).requested_by_user_id


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
        # 个人上限 1：第一个在跑，第二个在 worker 里排队，库里仍是 pending。
        worker.start([first, second], creation.batch_id, user_id=await _owner(factory, creation), user_limit=1)
        await _wait_status(factory, first, {"running"})
        assert (await _task(factory, second)).status == "pending"

        # 排队中的：web 直接写成已停止；worker 收到通知后丢掉排队的协程。
        assert await _request_cancel(factory, second) == "cancelled"
        await worker._cancel_from_notice(second)
        await _wait_until(lambda: _async_bool(second not in worker._tasks))
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
        # 模拟：任务已在库里被认领（running），但 worker 还没登记认领就收到了停止通知。
        worker.start([task_id], creation.batch_id, user_id=await _owner(factory, creation), user_limit=1)
        await _wait_status(factory, task_id, {"running"})
        worker._claimed.discard(task_id)
        assert await _request_cancel(factory, task_id) == "flagged"
        await worker._cancel_from_notice(task_id)
        # 只设了标志，没有直接取消协程：任务照常按停止收尾，不会卡在 running。
        assert task_id in worker._cancel_requested
        worker._claimed.add(task_id)
        await _wait_status(factory, task_id, {"cancelled"})

    _run(flow)


def test_startup_sweep_cancels_flagged_and_settles_stale_batch():
    async def flow(factory, worker, _web, _make_bus):
        flagged = await _create_batch(factory, ["ok"])
        flagged_id = flagged.runs[0].task_id
        async with factory() as session:
            assert await GenerationService(session).claim_task(flagged_id) is not None
        assert await _request_cancel(factory, flagged_id) == "flagged"

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
        worker.start(task_ids, creation.batch_id, user_id=await _owner(factory, creation), user_limit=1)
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
        worker.start(task_ids, creation.batch_id, user_id=await _owner(factory, creation), user_limit=1)
        received = await asyncio.wait_for(consumer, timeout=10)
        assert [item.event for item in received if item is not HEARTBEAT][-1] == "batch_done"

    _run(flow)


def test_scan_drops_queued_task_cancelled_without_notice():
    async def flow(factory, worker, _web, _make_bus):
        creation = await _create_batch(factory, ["slow-a", "slow-b"])
        first, second = (run.task_id for run in creation.runs)
        worker.start([first, second], creation.batch_id, user_id=await _owner(factory, creation), user_limit=1)
        await _wait_status(factory, first, {"running"})
        # 第二个在 worker 里排队；web 把它写成已停止，但停止通知丢了。
        assert await _request_cancel(factory, second) == "cancelled"
        assert second in worker._tasks
        await worker.scan_once()
        await _wait_until(lambda: _async_bool(second not in worker._tasks))
        assert (await _task(factory, second)).status == "cancelled"
        assert await _count_events(factory, second, "cancelled") == 1
        # 在跑的不受影响。
        assert (await _task(factory, first)).status == "running"

    _run(flow)
