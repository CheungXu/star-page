"""文档解析子进程隔离单测：正常解析、超时强杀、内存上限拦截、取消即刻回收、并发信号量排队。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from doc_fixtures import build_pdf, jpeg_bytes  # noqa: E402

from app.services.document_parser.child import (  # noqa: E402
    parse_subprocess_stats,
    reset_parse_subprocess_stats,
    run_parse_subprocess,
)
from app.services.document_parser.errors import DocumentParseError  # noqa: E402


def _sample_pdf_bytes() -> bytes:
    return build_pdf(
        [
            {
                "texts": [(60, 800, 20, "Quarterly Summary"), (50, 760, 10, "Growth details " * 12)],
                "image": (60, 260, 220, 160, jpeg_bytes(440, 320)),
            }
        ]
    )


def _live_parse_children_of_self() -> list[int]:
    """检查当前测试进程名下是否还有存活的 child.py 子进程。"""
    my_pid = str(os.getpid())
    proc = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True, check=True)
    alive: list[int] = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1] == my_pid and "app.services.document_parser.child" in parts[2]:
            alive.append(int(parts[0]))
    return alive


def test_subprocess_parses_real_pdf_and_records_rss(tmp_path: Path) -> None:
    reset_parse_subprocess_stats()
    pdf_path = tmp_path / "summary.pdf"
    pdf_path.write_bytes(_sample_pdf_bytes())

    async def run() -> None:
        doc = await run_parse_subprocess(
            filename="summary.pdf",
            extension=".pdf",
            input_path=pdf_path,
            timeout_seconds=30,
        )
        assert "Quarterly Summary" in doc.text
        assert len(doc.images) == 1

    asyncio.run(run())
    stats = parse_subprocess_stats()
    assert stats["parse_procs"] == 0
    assert stats["parse_waiting"] == 0
    assert stats["parse_last_rss_mb"] > 0
    assert stats["parse_last_ms"] > 0
    assert _live_parse_children_of_self() == []


def test_subprocess_timeout_kills_hung_child(tmp_path: Path) -> None:
    reset_parse_subprocess_stats()
    pdf_path = tmp_path / "hung.pdf"
    pdf_path.write_bytes(_sample_pdf_bytes())

    async def run() -> None:
        started = time.perf_counter()
        with pytest.raises(DocumentParseError) as caught:
            await run_parse_subprocess(
                filename="hung.pdf",
                extension=".pdf",
                input_path=pdf_path,
                timeout_seconds=0.35,
                extra_args=["--simulate-hang"],
            )
        elapsed = time.perf_counter() - started
        assert "解析超时" in caught.value.user_message
        assert elapsed < 3.0

    asyncio.run(run())
    stats = parse_subprocess_stats()
    assert stats["parse_timeouts"] == 1
    assert stats["parse_procs"] == 0
    assert _live_parse_children_of_self() == []


def test_subprocess_memory_limit_raises_readable_error(tmp_path: Path) -> None:
    reset_parse_subprocess_stats()
    pdf_path = tmp_path / "heavy.pdf"
    pdf_path.write_bytes(_sample_pdf_bytes())

    async def run() -> None:
        with pytest.raises(DocumentParseError) as caught:
            await run_parse_subprocess(
                filename="heavy.pdf",
                extension=".pdf",
                input_path=pdf_path,
                timeout_seconds=15,
                max_memory_mb=256,
                extra_args=["--simulate-oom"],
            )
        assert "过大或内容过于复杂" in caught.value.user_message

    asyncio.run(run())
    stats = parse_subprocess_stats()
    assert stats["parse_ooms"] == 1
    assert stats["parse_procs"] == 0
    assert _live_parse_children_of_self() == []


def test_subprocess_cancellation_kills_child_immediately(tmp_path: Path) -> None:
    reset_parse_subprocess_stats()
    pdf_path = tmp_path / "cancel.pdf"
    pdf_path.write_bytes(_sample_pdf_bytes())

    async def run() -> None:
        task = asyncio.create_task(
            run_parse_subprocess(
                filename="cancel.pdf",
                extension=".pdf",
                input_path=pdf_path,
                timeout_seconds=30,
                extra_args=["--simulate-hang"],
            )
        )
        deadline = time.perf_counter() + 5.0
        while parse_subprocess_stats()["parse_procs"] == 0:
            assert time.perf_counter() < deadline
            await asyncio.sleep(0.02)

        t0 = time.perf_counter()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.perf_counter() - t0 < 1.0

    asyncio.run(run())
    assert parse_subprocess_stats()["parse_procs"] == 0
    assert _live_parse_children_of_self() == []


def test_subprocess_semaphore_serializes_when_concurrency_is_one(tmp_path: Path) -> None:
    reset_parse_subprocess_stats()
    pdf_path = tmp_path / "seq.pdf"
    pdf_path.write_bytes(_sample_pdf_bytes())
    observed_waiting: list[int] = []

    async def run() -> None:
        first = asyncio.create_task(
            run_parse_subprocess(
                filename="a.pdf",
                extension=".pdf",
                input_path=pdf_path,
                timeout_seconds=30,
                concurrency=1,
            )
        )
        second = asyncio.create_task(
            run_parse_subprocess(
                filename="b.pdf",
                extension=".pdf",
                input_path=pdf_path,
                timeout_seconds=30,
                concurrency=1,
            )
        )
        while not (first.done() and second.done()):
            observed_waiting.append(int(parse_subprocess_stats()["parse_waiting"]))
            await asyncio.sleep(0.01)
        docs = await asyncio.gather(first, second)
        assert len(docs) == 2

    asyncio.run(run())
    assert max(observed_waiting, default=0) >= 1
    assert parse_subprocess_stats()["parse_procs"] == 0
    assert parse_subprocess_stats()["parse_waiting"] == 0


def test_subprocess_succeeds_even_if_stderr_contains_oom_keywords(tmp_path: Path) -> None:
    reset_parse_subprocess_stats()
    pdf_path = tmp_path / "noisy.pdf"
    pdf_path.write_bytes(_sample_pdf_bytes())

    async def run() -> None:
        doc = await run_parse_subprocess(
            filename="noisy.pdf",
            extension=".pdf",
            input_path=pdf_path,
            timeout_seconds=30,
            extra_args=["--simulate-noisy-stderr"],
        )
        assert "Quarterly Summary" in doc.text

    asyncio.run(run())
    stats = parse_subprocess_stats()
    assert stats["parse_ooms"] == 0
    assert stats["parse_procs"] == 0


def test_cancelling_queued_waiter_resets_waiting_count_and_preserves_concurrency(tmp_path: Path) -> None:
    reset_parse_subprocess_stats()
    pdf_path = tmp_path / "queue.pdf"
    pdf_path.write_bytes(_sample_pdf_bytes())

    async def run() -> None:
        holder = asyncio.create_task(
            run_parse_subprocess(
                filename="holder.pdf",
                extension=".pdf",
                input_path=pdf_path,
                timeout_seconds=30,
                concurrency=1,
                extra_args=["--simulate-hang"],
            )
        )
        deadline = time.perf_counter() + 5.0
        while parse_subprocess_stats()["parse_procs"] == 0:
            assert time.perf_counter() < deadline
            await asyncio.sleep(0.02)

        queued = asyncio.create_task(
            run_parse_subprocess(
                filename="queued.pdf",
                extension=".pdf",
                input_path=pdf_path,
                timeout_seconds=30,
                concurrency=1,
            )
        )
        while parse_subprocess_stats()["parse_waiting"] == 0:
            assert time.perf_counter() < deadline
            await asyncio.sleep(0.01)

        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert parse_subprocess_stats()["parse_waiting"] == 0

        # 第三个任务仍应受 concurrency=1 限制进入排队，证明名额未多放。
        third = asyncio.create_task(
            run_parse_subprocess(
                filename="third.pdf",
                extension=".pdf",
                input_path=pdf_path,
                timeout_seconds=30,
                concurrency=1,
            )
        )
        await asyncio.sleep(0.05)
        assert parse_subprocess_stats()["parse_procs"] == 1
        assert parse_subprocess_stats()["parse_waiting"] == 1

        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder
        doc = await third
        assert "Quarterly Summary" in doc.text

    asyncio.run(run())
    stats = parse_subprocess_stats()
    assert stats["parse_waiting"] == 0
    assert stats["parse_procs"] == 0
    assert _live_parse_children_of_self() == []

