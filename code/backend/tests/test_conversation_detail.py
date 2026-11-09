"""会话详情纯逻辑单测：节点生成用时。

    .venv/bin/python -m pytest tests/test_conversation_detail.py
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes_conversations import _task_duration_ms  # noqa: E402


def test_duration_from_task_timestamps():
    started = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    task = SimpleNamespace(started_at=started, finished_at=started + timedelta(seconds=83, milliseconds=250))
    assert _task_duration_ms(task) == 83_250


def test_duration_missing_when_unfinished():
    started = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    assert _task_duration_ms(None) is None
    assert _task_duration_ms(SimpleNamespace(started_at=None, finished_at=started)) is None
    assert _task_duration_ms(SimpleNamespace(started_at=started, finished_at=None)) is None


def test_duration_never_negative():
    started = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    task = SimpleNamespace(started_at=started, finished_at=started - timedelta(seconds=1))
    assert _task_duration_ms(task) == 0
