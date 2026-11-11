"""把指定批次里没完成的生成任务交给下一次后端启动扫尾重跑。

后端启动时只自动重跑「最近活动在 GENERATION_RESUME_WINDOW_MINUTES（默认 120 分钟）内」的中断任务，
更早的标失败。这个脚本给指定批次里每个没完成的任务写一条 requeued 事件，把最近活动刷成现在，
然后在窗口内重启后端，扫尾就会把它们清掉旧进度、从头重跑一次（每个任务只自动重跑一次）。

用法（在仓库根目录，生产库）：

    code/backend/.venv/bin/python script/requeue_stuck_generations.py --batch <uuid> [--batch <uuid> ...] [--dry-run]
    systemctl restart star-page-backend.service   # 必须在窗口内

批次 id 可以只写前 8 位，脚本会按前缀查找，前缀不唯一时报错。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "code" / "backend"
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

from sqlalchemy import String, cast, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.models.entities import GenerationBatch, GenerationEvent, GenerationTask  # noqa: E402

UNFINISHED = ("pending", "running")


async def _resolve_batch(session, raw: str) -> uuid.UUID:
    text = raw.strip().lower()
    try:
        return uuid.UUID(text)
    except ValueError:
        pass
    result = await session.execute(
        select(GenerationBatch.id).where(cast(GenerationBatch.id, String).like(f"{text}%")).limit(2)
    )
    matches = list(result.scalars().all())
    if len(matches) != 1:
        raise SystemExit(f"批次前缀 {raw} 匹配到 {len(matches)} 个批次，请写完整 id")
    return matches[0]


async def main() -> None:
    parser = argparse.ArgumentParser(description="把卡住的生成任务交给下次后端启动重跑")
    parser.add_argument("--batch", action="append", required=True, help="generation_batches.id，可写前 8 位，可重复")
    parser.add_argument("--dry-run", action="store_true", help="只列出会处理的任务，不写库")
    args = parser.parse_args()

    marked = 0
    async with AsyncSessionLocal() as session:
        for raw in args.batch:
            batch_id = await _resolve_batch(session, raw)
            batch = await session.get(GenerationBatch, batch_id)
            prompt = ((batch.user_prompt or batch.prompt) if batch else "")[:30]
            print(f"批次 {batch_id}  {prompt}")
            result = await session.execute(
                select(GenerationTask.id, GenerationTask.model_key, GenerationTask.status, GenerationTask.retry_count)
                .where(GenerationTask.batch_id == batch_id)
                .order_by(GenerationTask.model_key)
            )
            for task_id, model_key, status, retry_count in result.all():
                if status not in UNFINISHED:
                    print(f"  跳过 {model_key}：{status}")
                    continue
                if (retry_count or 0) >= 1 and status == "running":
                    print(f"  注意 {model_key}：已经自动重跑过一次，扫尾会标失败，不会再跑")
                print(f"  {'将标记' if args.dry_run else '标记'} {model_key}：{status}")
                if not args.dry_run:
                    session.add(GenerationEvent(task_id=task_id, event_type="requeued", payload={"type": "requeued"}))
                marked += 1
        if not args.dry_run:
            await session.commit()

    window = get_settings().generation_resume_window_minutes
    if args.dry_run:
        print(f"预演结束：{marked} 个任务会被标记。")
        return
    print(f"已标记 {marked} 个任务。请在 {window} 分钟内重启后端：systemctl restart star-page-backend.service")


if __name__ == "__main__":
    asyncio.run(main())
