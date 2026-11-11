"""把已结束任务里重复的生成事件收成一行。

默认只统计，不写库。加上 --apply 才合并，并在结束后 VACUUM (ANALYZE) generation_events。

只处理已经是终态、且 finished_at 早于 10 分钟的任务。每个任务一个事务：

- 连续的 reasoning_delta 合成一行，文本按序拼接，保留这段最后一行的 sequence。
- 连续的 progress 里，步骤和状态都相同的收成最后一行（输出过程中每 50 token 记一次的就是这种），
  保留最后一行的内容和序号。思考完成、输出完成、上传完成这些节点状态不同，各自保留。

其它事件不动。保留最后一行的序号后，断点续传只会多发、不会漏发。重复执行不会再改已经合并过的任务。

用法（在仓库根目录）：

    code/backend/.venv/bin/python script/compact_generation_events.py
    code/backend/.venv/bin/python script/compact_generation_events.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "code" / "backend"
if __name__ == "__main__":
    os.chdir(BACKEND)
backend = str(BACKEND)
if backend not in sys.path:
    sys.path.insert(0, backend)

from sqlalchemy import delete, func, or_, select  # noqa: E402

from app.models.entities import GenerationEvent, GenerationTask  # noqa: E402

GRACE = timedelta(minutes=10)
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


@dataclass
class CompactStats:
    candidate_tasks: int = 0
    tasks_changed: int = 0
    reasoning_groups: int = 0
    reasoning_rows_removed: int = 0
    progress_groups: int = 0
    progress_rows_removed: int = 0

    @property
    def groups(self) -> int:
        return self.reasoning_groups + self.progress_groups

    @property
    def rows_removed(self) -> int:
        return self.reasoning_rows_removed + self.progress_rows_removed


def _reasoning_groups(rows: list[GenerationEvent]) -> list[list[GenerationEvent]]:
    groups: list[list[GenerationEvent]] = []
    current: list[GenerationEvent] = []
    for row in rows:
        if row.event_type == "reasoning_delta":
            current.append(row)
            continue
        if len(current) > 1:
            groups.append(current)
        current = []
    if len(current) > 1:
        groups.append(current)
    return groups


def _progress_key(row: GenerationEvent) -> tuple[str, str] | None:
    payload = row.payload or {}
    step = payload.get("step")
    status = payload.get("status")
    if not step or not status:
        return None
    return (str(step), str(status))


def _progress_groups(rows: list[GenerationEvent]) -> list[list[GenerationEvent]]:
    """同一段里步骤和状态都相同的进度收成一组。状态一变（例如输出中变成输出完成）就断开。"""
    groups: list[list[GenerationEvent]] = []
    current: list[GenerationEvent] = []
    current_key: tuple[str, str] | None = None
    for row in rows:
        key = _progress_key(row) if row.event_type == "progress" else None
        if key is not None and key == current_key:
            current.append(row)
            continue
        if len(current) > 1:
            groups.append(current)
        current = [row] if key is not None else []
        current_key = key
    if len(current) > 1:
        groups.append(current)
    return groups


async def compact_generation_events(session_factory, *, apply: bool, grace: timedelta = GRACE) -> CompactStats:
    """合并终态任务里连续的思考行和重复进度行。apply 为假时只统计。"""
    cutoff = datetime.now(UTC) - grace
    stats = CompactStats()
    async with session_factory() as session:
        reasoning_count = func.count().filter(GenerationEvent.event_type == "reasoning_delta")
        progress_count = func.count().filter(GenerationEvent.event_type == "progress")
        result = await session.execute(
            select(GenerationEvent.task_id)
            .join(GenerationTask, GenerationTask.id == GenerationEvent.task_id)
            .where(
                GenerationTask.status.in_(TERMINAL_STATUSES),
                GenerationTask.finished_at.is_not(None),
                GenerationTask.finished_at < cutoff,
                GenerationEvent.event_type.in_(("reasoning_delta", "progress")),
            )
            .group_by(GenerationEvent.task_id)
            .having(or_(reasoning_count > 1, progress_count > 1))
        )
        task_ids = list(result.scalars().all())
    stats.candidate_tasks = len(task_ids)

    for task_id in task_ids:
        async with session_factory() as session:
            result = await session.execute(
                select(GenerationEvent).where(GenerationEvent.task_id == task_id).order_by(GenerationEvent.sequence)
            )
            rows = list(result.scalars().all())
            reasoning_groups = _reasoning_groups(rows)
            progress_groups = _progress_groups(rows)
            if not reasoning_groups and not progress_groups:
                continue
            stats.tasks_changed += 1
            stats.reasoning_groups += len(reasoning_groups)
            stats.reasoning_rows_removed += sum(len(group) - 1 for group in reasoning_groups)
            stats.progress_groups += len(progress_groups)
            stats.progress_rows_removed += sum(len(group) - 1 for group in progress_groups)
            if not apply:
                continue
            for group in reasoning_groups:
                merged = "".join(str((row.payload or {}).get("text") or "") for row in group)
                last = group[-1]
                payload = dict(last.payload or {})
                payload["text"] = merged
                if not payload.get("type"):
                    payload["type"] = "reasoning_delta"
                last.payload = payload
            removed_ids = [row.id for group in (*reasoning_groups, *progress_groups) for row in group[:-1]]
            # asyncpg 一条语句最多 32767 个参数，大任务要分批删。
            for start in range(0, len(removed_ids), 5000):
                chunk = removed_ids[start : start + 5000]
                await session.execute(delete(GenerationEvent).where(GenerationEvent.id.in_(chunk)))
            await session.commit()
    return stats


def format_stats(stats: CompactStats, *, apply: bool) -> str:
    minutes = int(GRACE.total_seconds() // 60)
    verb = "已合并" if apply else "可合并"
    return (
        f"结束超过 {minutes} 分钟的终态任务里，思考或进度多于 1 行的有 {stats.candidate_tasks} 个；"
        f"{verb} {stats.tasks_changed} 个任务。"
        f"思考 {stats.reasoning_groups} 段、删除 {stats.reasoning_rows_removed} 行；"
        f"进度 {stats.progress_groups} 段、删除 {stats.progress_rows_removed} 行。"
    )


async def vacuum_generation_events() -> None:
    import asyncpg

    from app.core.config import get_settings

    settings = get_settings()
    dsn = settings.async_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn=dsn)
    try:
        await connection.execute("VACUUM (ANALYZE) generation_events")
    finally:
        await connection.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="合并已结束任务里连续的思考碎片和重复进度")
    parser.add_argument("--apply", action="store_true", help="真正写入。默认只统计")
    args = parser.parse_args()

    from app.core.database import AsyncSessionLocal

    stats = await compact_generation_events(AsyncSessionLocal, apply=args.apply)
    print(format_stats(stats, apply=args.apply))
    if not args.apply:
        print("未写入。确认后加 --apply 执行。")
        return
    await vacuum_generation_events()
    print("已执行 VACUUM (ANALYZE) generation_events。")


if __name__ == "__main__":
    asyncio.run(main())
