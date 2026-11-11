"""后台生成执行器集成测试：认领只成功一次、跑完并经事件流续传、协作式停止、硬取消、启动扫尾。

模型调用、上传、计费、技能路由都换成假的，只验证任务生命周期和数据库状态。需要一个跑过迁移的空库：

    TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/stars_page_test \
        .venv/bin/python -m pytest tests/test_generation_runner_db.py

未设置 TEST_DATABASE_URL 时跳过。不要指向线上库：测试会写入用户、批次和任务，启动扫尾会改所有未结束任务。
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="未设置 TEST_DATABASE_URL")

if TEST_DATABASE_URL:
    from sqlalchemy import func, select, update  # noqa: E402
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

    from app.models.entities import GenerationBatch, GenerationEvent, GenerationTask, Page, PageVersion, User  # noqa: E402
    from app.services import generation_runner as runner_module  # noqa: E402
    from app.services import generation_service as service_module  # noqa: E402
    from app.services.billing.account import BillingService  # noqa: E402
    from app.services.generation_events import HEARTBEAT, stream_task_events  # noqa: E402
    from app.services.generation_runner import GenerationRunner  # noqa: E402
    from app.services.generation_service import GenerationService  # noqa: E402
    from app.services.llm.types import LlmStreamChunk  # noqa: E402
    from app.services.skills.selector import SkillSelectionResult  # noqa: E402

HTML = "<!DOCTYPE html><html><head><title>测试</title></head><body><p>ok</p></body></html>"


class FakeClient:
    """按模型 key 的前缀决定行为：ok 正常输出，slow 一直慢慢思考，hang 吐一段后卡住。"""

    def __init__(self, model_key: str) -> None:
        self.mode = model_key.split("-")[0]

    async def stream_text(self, _messages):
        if self.mode == "ok":
            yield LlmStreamChunk(type="reasoning_delta", provider="fake", model="fake", reasoning_text="想一想")
            yield LlmStreamChunk(type="text_delta", provider="fake", model="fake", text=HTML)
            return
        if self.mode == "slow":
            for index in range(2000):
                await asyncio.sleep(0.02)
                yield LlmStreamChunk(type="reasoning_delta", provider="fake", model="fake", reasoning_text=str(index))
            return
        yield LlmStreamChunk(type="reasoning_delta", provider="fake", model="fake", reasoning_text="开始")
        await asyncio.sleep(3600)


async def _noop(*_args, **_kwargs) -> None:
    return None


async def _no_skill(self, *, page, task):
    return SkillSelectionResult(None, None)


async def _fake_upload(self, page, html):
    return uuid.uuid4(), f"pages/{page.id}/test/index.html"


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    monkeypatch.setattr(BillingService, "ensure_can_start_batch", _noop)
    monkeypatch.setattr(BillingService, "record_anon_batch_started", _noop)
    monkeypatch.setattr(GenerationService, "_resolve_model_keys", lambda self, keys, allowed_keys=None: list(keys))
    monkeypatch.setattr(GenerationService, "_resolve_task_skill_key", _no_skill)
    monkeypatch.setattr(GenerationService, "_upload_page_html", _fake_upload)
    monkeypatch.setattr(GenerationService, "_settle_generation_billing", _noop)
    monkeypatch.setattr(service_module, "create_llm_client", lambda model_key: FakeClient(model_key))
    monkeypatch.setattr(runner_module, "HARD_CANCEL_GRACE_SECONDS", 0.2)


def _run(flow) -> None:
    async def wrapper() -> None:
        engine = create_async_engine(TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1))
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        runner = GenerationRunner(session_factory=factory)
        try:
            await flow(factory, runner)
        finally:
            await runner.shutdown()
            await engine.dispose()

    asyncio.run(wrapper())


async def _create_batch(factory, models: list[str]):
    async with factory() as session:
        user = User(username=f"runner-{uuid.uuid4().hex[:12]}", display_name="执行器测试")
        session.add(user)
        await session.commit()
        creation = await GenerationService(session).create_batch(prompt="做一个测试页面", selected_model_keys=models, user=user)
    return creation


async def _task(factory, task_id) -> GenerationTask:
    async with factory() as session:
        return await session.get(GenerationTask, task_id)


async def _event_types(factory, task_id) -> list[str]:
    async with factory() as session:
        result = await session.execute(
            select(GenerationEvent.event_type).where(GenerationEvent.task_id == task_id).order_by(GenerationEvent.sequence)
        )
        return list(result.scalars().all())


async def _wait_until(check, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("等待超时")


async def _wait_idle(runner: GenerationRunner, timeout: float = 10.0) -> None:
    async def idle() -> bool:
        return not runner._tasks

    await _wait_until(idle, timeout)


def test_claim_succeeds_only_once():
    async def flow(factory, _runner):
        creation = await _create_batch(factory, ["ok"])
        task_id = creation.runs[0].task_id

        async def claim():
            async with factory() as session:
                return await GenerationService(session).claim_task(task_id)

        results = await asyncio.gather(claim(), claim(), claim())
        assert sum(result is not None for result in results) == 1
        assert (await _task(factory, task_id)).status == "running"

    _run(flow)


def test_runs_in_background_and_stream_resumes():
    async def flow(factory, runner):
        creation = await _create_batch(factory, ["ok-a", "ok-b"])
        task_ids = [run.task_id for run in creation.runs]
        runner.start(task_ids, creation.batch_id)
        await _wait_idle(runner)

        for task_id in task_ids:
            task = await _task(factory, task_id)
            assert task.status == "succeeded"
            types = await _event_types(factory, task_id)
            assert types[-1] == "completed"
            assert "reasoning_delta" in types
        async with factory() as session:
            batch = await session.get(GenerationBatch, creation.batch_id)
            assert batch.status == "succeeded"

        # 生成早已结束，再订阅：完整回放后以 batch_done 结束。
        received = []
        async for item in stream_task_events(
            session_factory=factory, runner=runner, task_ids=task_ids, signal_key=creation.batch_id
        ):
            assert item is not HEARTBEAT
            received.append(item)
        assert received[-1].event == "batch_done"
        completed = [item for item in received if item.event == "completed"]
        assert {item.data["task_id"] for item in completed} == {str(task_id) for task_id in task_ids}
        cursor = next(item.id for item in reversed(received) if item.id is not None)

        # 带着最后的游标续传：两个任务都已收尾，直接 batch_done。
        resumed = [
            item
            async for item in stream_task_events(
                session_factory=factory,
                runner=runner,
                task_ids=task_ids,
                signal_key=creation.batch_id,
                last_event_id=cursor,
            )
        ]
        assert [item.event for item in resumed] == ["batch_done"]

    _run(flow)


def test_live_stream_follows_running_task():
    async def flow(factory, runner):
        creation = await _create_batch(factory, ["ok"])
        task_ids = [creation.runs[0].task_id]

        async def consume():
            return [
                item
                async for item in stream_task_events(
                    session_factory=factory,
                    runner=runner,
                    task_ids=task_ids,
                    signal_key=creation.batch_id,
                    heartbeat_seconds=0.5,
                    debounce_seconds=0.01,
                )
            ]

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.1)
        runner.start(task_ids, creation.batch_id)
        received = await asyncio.wait_for(consumer, timeout=10)
        events = [item.event for item in received if item is not HEARTBEAT]
        assert "completed" in events and events[-1] == "batch_done"

    _run(flow)


def test_cooperative_cancel_marks_cancelled_without_version():
    async def flow(factory, runner):
        creation = await _create_batch(factory, ["slow"])
        task_id = creation.runs[0].task_id
        page_id = creation.runs[0].page_id
        runner.start([task_id], creation.batch_id)

        async def thinking() -> bool:
            return (await _event_types(factory, task_id)).count("reasoning_delta") >= 3

        await _wait_until(thinking)
        assert await runner.cancel(task_id) is True
        await _wait_idle(runner)

        task = await _task(factory, task_id)
        assert task.status == "cancelled"
        assert (await _event_types(factory, task_id))[-1] == "cancelled"
        async with factory() as session:
            versions = await session.scalar(select(func.count(PageVersion.id)).where(PageVersion.page_id == page_id))
            batch = await session.get(GenerationBatch, creation.batch_id)
        assert versions == 0
        assert batch.status == "cancelled"

    _run(flow)


def test_hard_cancel_when_model_stalls():
    async def flow(factory, runner):
        creation = await _create_batch(factory, ["hang"])
        task_id = creation.runs[0].task_id
        runner.start([task_id], creation.batch_id)

        async def started() -> bool:
            return "reasoning_delta" in await _event_types(factory, task_id)

        await _wait_until(started)
        assert await runner.cancel(task_id) is True
        await _wait_idle(runner, timeout=5)

        task = await _task(factory, task_id)
        assert task.status == "cancelled"
        assert task.error_message == runner_module.CANCELLED_ERROR_MESSAGE
        assert (await _event_types(factory, task_id))[-1] == "cancelled"

    _run(flow)


def test_cancel_queued_task_returns_false():
    async def flow(factory, runner):
        runner._semaphore = asyncio.Semaphore(0)  # 永远排队
        creation = await _create_batch(factory, ["ok"])
        task_id = creation.runs[0].task_id
        runner.start([task_id], creation.batch_id)
        await asyncio.sleep(0.05)
        assert await runner.cancel(task_id) is False
        async with factory() as session:
            changed = await GenerationService(session).finish_unfinished_task(
                task_id, status="cancelled", message="用户停止了生成", user_message="已停止生成"
            )
        assert changed is True
        await _wait_idle(runner)
        assert (await _task(factory, task_id)).status == "cancelled"

    _run(flow)


def test_http_batch_stream_and_cancel(monkeypatch):
    import httpx

    from app.api import routes_generation
    from app.main import app

    async def flow(factory, runner):
        creation = await _create_batch(factory, ["ok-a", "slow"])
        async with factory() as session:
            owner = await session.get(User, (await session.get(GenerationTask, creation.runs[0].task_id)).requested_by_user_id)
        stranger = User(id=uuid.uuid4(), username="stranger", display_name="别人")
        actor = {"user": owner}

        async def fake_actor(_session, _request):
            return actor["user"]

        monkeypatch.setattr(routes_generation, "AsyncSessionLocal", factory)
        monkeypatch.setattr(routes_generation, "generation_runner", runner)
        monkeypatch.setattr(routes_generation, "get_optional_actor", fake_actor)

        ids = {run.model_key: run.task_id for run in creation.runs}
        runner.start(list(ids.values()), creation.batch_id)

        async def ok_done() -> bool:
            return (await _task(factory, ids["ok-a"])).status == "succeeded"

        await _wait_until(ok_done)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            actor["user"] = stranger
            assert (await client.post(f"/api/generations/batches/{creation.batch_id}/cancel")).status_code == 404
            assert (await client.get(f"/api/generations/batches/{creation.batch_id}/events")).status_code == 404

            actor["user"] = owner
            response = await client.post(f"/api/generations/batches/{creation.batch_id}/cancel")
            assert response.status_code == 200
            assert response.json()["stopping"] == 1
            await _wait_idle(runner)

            body = (await client.get(f"/api/generations/batches/{creation.batch_id}/events")).text
        assert "event: completed" in body and "event: cancelled" in body
        assert body.rstrip().endswith('data: {"type": "batch_done"}')
        assert "\nid: " in body or body.startswith("id: ")
        assert (await _task(factory, ids["slow"])).status == "cancelled"

    _run(flow)


def test_startup_sweep_reruns_recent_and_fails_stale(monkeypatch):
    async def flow(factory, runner):
        creation = await _create_batch(factory, ["r1", "r2", "r3", "r4"])
        ids = {run.model_key: run.task_id for run in creation.runs}
        pages = {run.model_key: run.page_id for run in creation.runs}
        now = datetime.now(UTC)
        async with factory() as session:
            # r1：刚被打断，有旧事件 -> 重跑
            # r2：已经自动重跑过一次 -> 标失败
            # r3：三天前就停了 -> 标失败
            # r4：一直在排队没开始 -> 直接排队
            for key in ("r1", "r2", "r3"):
                await session.execute(
                    update(GenerationTask)
                    .where(GenerationTask.id == ids[key])
                    .values(status="running", started_at=now - timedelta(minutes=5))
                )
                session.add(GenerationEvent(task_id=ids[key], event_type="reasoning_delta", payload={"text": "旧"}))
            await session.execute(update(GenerationTask).where(GenerationTask.id == ids["r2"]).values(retry_count=1))
            await session.commit()
            old = now - timedelta(days=3)
            await session.execute(
                update(GenerationTask).where(GenerationTask.id == ids["r3"]).values(created_at=old, started_at=old)
            )
            await session.execute(
                update(GenerationEvent).where(GenerationEvent.task_id == ids["r3"]).values(created_at=old)
            )
            await session.commit()

        started: list[uuid.UUID] = []
        monkeypatch.setattr(runner, "start", lambda task_ids, batch_id: started.extend(task_ids))
        await runner.startup_sweep()

        r1 = await _task(factory, ids["r1"])
        assert (r1.status, r1.retry_count, r1.started_at) == ("pending", 1, None)
        assert await _event_types(factory, ids["r1"]) == ["restarted"]
        for key in ("r2", "r3"):
            task = await _task(factory, ids[key])
            assert task.status == "failed"
            assert task.error_message == runner_module.INTERRUPTED_ERROR_MESSAGE
            assert (await _event_types(factory, ids[key]))[-1] == "failed"
        r4 = await _task(factory, ids["r4"])
        assert (r4.status, r4.retry_count) == ("pending", 0)
        assert ids["r1"] in started and ids["r4"] in started
        assert ids["r2"] not in started and ids["r3"] not in started
        async with factory() as session:
            assert (await session.get(Page, pages["r1"])).status == "generating"
            assert (await session.get(Page, pages["r2"])).status == "failed"
            batch = await session.get(GenerationBatch, creation.batch_id)
        assert batch.status == "running"

    _run(flow)
