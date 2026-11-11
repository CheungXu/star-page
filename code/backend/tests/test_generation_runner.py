"""后台生成执行器与事件流的纯逻辑单测：扫尾分流、事件合并、续传游标、唤醒器。不需要数据库。"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.generation_events import (  # noqa: E402
    EventRow,
    coalesce_events,
    decode_cursor,
    encode_cursor,
    ordered_task_ids,
)
from app.models.entities import User  # noqa: E402
from app.services.generation_runner import (  # noqa: E402
    BatchSignal,
    FifoGate,
    InterruptedTask,
    plan_sweep,
    user_concurrency_limit,
)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=120)


def _task(
    *,
    status: str = "running",
    retry: int = 0,
    created_ago: timedelta,
    started_ago: timedelta | None = None,
    event_ago: timedelta | None = None,
) -> InterruptedTask:
    return InterruptedTask(
        task_id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        status=status,
        retry_count=retry,
        created_at=NOW - created_ago,
        started_at=NOW - started_ago if started_ago is not None else None,
        last_event_at=NOW - event_ago if event_ago is not None else None,
    )


def test_plan_sweep_reruns_recent_interrupted_once():
    recent = _task(created_ago=timedelta(minutes=10), started_ago=timedelta(minutes=10), event_ago=timedelta(minutes=1))
    already_retried = _task(
        retry=1, created_ago=timedelta(minutes=10), started_ago=timedelta(minutes=5), event_ago=timedelta(minutes=1)
    )
    plan = plan_sweep([recent, already_retried], now=NOW, window=WINDOW)
    assert plan.rerun == [recent]
    assert plan.fail == [already_retried]
    assert plan.start == []


def test_plan_sweep_fails_stale_and_starts_never_started():
    stale = _task(created_ago=timedelta(days=2), started_ago=timedelta(days=2), event_ago=timedelta(days=2))
    queued = _task(status="pending", created_ago=timedelta(minutes=3))
    old_queued = _task(status="pending", created_ago=timedelta(days=30))
    plan = plan_sweep([stale, queued, old_queued], now=NOW, window=WINDOW)
    assert plan.start == [queued]
    assert plan.fail == [stale, old_queued]
    assert plan.rerun == []


def test_plan_sweep_uses_latest_activity_for_requeued_tasks():
    # 12 小时前创建、早就停了，但刚被 requeue 脚本写过一条事件：应当重跑。
    requeued = _task(created_ago=timedelta(hours=12), started_ago=timedelta(hours=12), event_ago=timedelta(seconds=30))
    # 从没开始过的 pending，被 requeue 后同样只是排队，不消耗重跑次数。
    requeued_pending = _task(status="pending", created_ago=timedelta(hours=12), event_ago=timedelta(seconds=30))
    plan = plan_sweep([requeued, requeued_pending], now=NOW, window=WINDOW)
    assert plan.rerun == [requeued]
    assert plan.start == [requeued_pending]


def test_coalesce_merges_reasoning_per_task_and_keeps_task_order():
    a, b = uuid.uuid4(), uuid.uuid4()
    rows = [
        EventRow(a, 1, "reasoning_delta", {"type": "reasoning_delta", "text": "甲"}),
        EventRow(b, 2, "reasoning_delta", {"type": "reasoning_delta", "text": "x"}),
        EventRow(a, 3, "reasoning_delta", {"type": "reasoning_delta", "text": "乙"}),
        EventRow(b, 4, "progress", {"type": "progress", "step": "model_output", "cost": {"total": 1}}),
        EventRow(b, 5, "reasoning_delta", {"type": "reasoning_delta", "text": "y"}),
        EventRow(a, 6, "completed", {"type": "completed", "page_id": "p"}),
    ]
    events = coalesce_events(rows)
    assert [(event.event, event.data.get("text"), event.data["task_id"]) for event in events] == [
        ("reasoning_delta", "甲乙", str(a)),
        ("reasoning_delta", "x", str(b)),
        ("progress", None, str(b)),
        ("reasoning_delta", "y", str(b)),
        ("completed", None, str(a)),
    ]
    progress = events[2]
    assert "cost" not in progress.data
    assert all(event.id is None for event in events)


def test_cursor_roundtrip_and_fallbacks():
    ids = ordered_task_ids([uuid.uuid4() for _ in range(3)])
    cursors = {ids[0]: 10, ids[1]: 0, ids[2]: 25}
    raw = encode_cursor(ids, cursors)
    assert decode_cursor(raw, ids) == cursors
    # 单个数字：旧事件流的 id，当作所有任务的共同起点。
    assert decode_cursor("42", ids) == {task_id: 42 for task_id in ids}
    # 看不懂或长度不对：从头开始。
    assert decode_cursor("abc", ids) == {task_id: 0 for task_id in ids}
    assert decode_cursor("1.2", ids) == {task_id: 0 for task_id in ids}
    assert decode_cursor(None, ids) == {task_id: 0 for task_id in ids}
    single = ordered_task_ids([uuid.uuid4()])
    assert decode_cursor("7", single) == {single[0]: 7}


def test_ordered_task_ids_is_stable():
    ids = [uuid.uuid4() for _ in range(5)]
    assert ordered_task_ids(ids) == ordered_task_ids(list(reversed(ids)))


def test_batch_signal_wakes_waiter_taken_before_notify():
    async def run() -> bool:
        signal = BatchSignal()
        waiter = signal.waiter()
        signal.notify()
        await asyncio.wait_for(waiter.wait(), timeout=0.5)
        # notify 之后换了新的 waiter，新 waiter 还没被触发。
        return signal.waiter().is_set()

    assert asyncio.run(run()) is False


def test_fifo_gate_queues_in_order_and_backfills():
    async def run() -> list[str]:
        gate = FifoGate(2)
        log: list[str] = []
        assert gate.enter() is None and gate.enter() is None
        third, fourth = gate.enter(), gate.enter()
        assert third is not None and fourth is not None and gate.active == 2
        gate.release()
        await asyncio.sleep(0)
        log.append(f"third={third.done()} fourth={fourth.done()} active={gate.active}")
        gate.release()
        log.append(f"fourth={fourth.done()} active={gate.active}")
        return log

    assert asyncio.run(run()) == ["third=True fourth=False active=2", "fourth=True active=2"]


def test_fifo_gate_abandon_and_raise_limit():
    async def run() -> tuple[int, int, bool]:
        gate = FifoGate(1)
        assert gate.enter() is None
        waiting = gate.enter()
        later = gate.enter()
        # 还没轮到就放弃：作废排号，名额不变，后面的人接着排。
        gate.abandon(waiting)
        gate.release()
        after_release = gate.active
        assert later.done()
        # 已经分到名额再放弃：名额还回去。
        gate.abandon(later)
        after_abandon = gate.active
        # 上限调大后，排队的立即补上。
        gate.enter()
        queued = gate.enter()
        gate.set_limit(2)
        return after_release, after_abandon, queued.done()

    assert asyncio.run(run()) == (1, 0, True)


def test_user_concurrency_limit_prefers_user_override():
    assert user_concurrency_limit(User(generation_concurrency=None)) == 10
    assert user_concurrency_limit(User(generation_concurrency=0)) == 10
    assert user_concurrency_limit(User(generation_concurrency=30)) == 30
