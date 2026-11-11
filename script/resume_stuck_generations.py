"""把卡在 running、还没写出页面的生成任务补跑完。

前端按会话详情里的任务状态和页面地址展示。补跑仍写回原来的任务和页面，
用户重新打开这条历史就能看到结果，不需要再提交一次。

后端有了后台执行器和启动扫尾之后，优先用 requeue_stuck_generations.py 加一次后端重启，
由后端自己重跑。本脚本在脚本进程里直接调用模型，只在后端没法接手时用，且不要对后端正在跑的任务用。

用法（在仓库根目录）：

    code/backend/.venv/bin/python script/resume_stuck_generations.py --batch-id <uuid>
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

# 公开地址只在进程环境里，配置文件不带。必须在导入 app 之前写上。
os.environ.setdefault("PUBLIC_BASE_URL", "https://stars-page.com")
os.environ.setdefault("FRONTEND_ORIGIN", "https://stars-page.com")
os.environ.setdefault("PGDATABASE", "stars_page")

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.entities import GenerationTask, Page
from app.services.generation_service import GenerationService


async def _load_running_tasks(batch_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(GenerationTask.id, GenerationTask.model_key, GenerationTask.status, GenerationTask.finished_at)
            .where(GenerationTask.batch_id == batch_id)
            .order_by(GenerationTask.model_key)
        )
        rows = result.all()
    if not rows:
        raise SystemExit(f"批次 {batch_id} 没有生成任务")

    picked: list[tuple[uuid.UUID, str]] = []
    for task_id, model_key, status, finished_at in rows:
        if status == "running" and finished_at is None:
            picked.append((task_id, model_key or "未知模型"))
            print(f"待补跑 {model_key} {task_id}", flush=True)
        else:
            print(f"跳过 {model_key}，当前状态 {status}", flush=True)
    return picked


async def _parent_html_ready(task_id: uuid.UUID) -> bool:
    async with AsyncSessionLocal() as session:
        service = GenerationService(session)
        task = await session.get(GenerationTask, task_id)
        if task is None:
            return False
        page = await session.get(Page, task.page_id)
        if page is None or page.parent_page_id is None:
            return True
        html = await service._load_node_html(page.parent_page_id)
        return bool(html)


async def _reset_for_retry(task_id: uuid.UUID) -> None:
    """失败后把同一条任务放回 running，页面保持生成中，方便再跑一次。"""
    async with AsyncSessionLocal() as session:
        task = await session.get(GenerationTask, task_id)
        if task is None or task.status == "succeeded":
            return
        page = await session.get(Page, task.page_id)
        task.status = "running"
        task.error_message = None
        task.finished_at = None
        if page is not None and page.status != "ready":
            page.status = "generating"
        await session.commit()


async def _run_once(task_id: uuid.UUID, model_key: str) -> str:
    async with AsyncSessionLocal() as session:
        service = GenerationService(session)
        task = await session.get(GenerationTask, task_id)
        if task is None:
            print(f"[{model_key}] 任务不存在", flush=True)
            return "missing"
        if task.status == "succeeded":
            print(f"[{model_key}] 已经成功，跳过", flush=True)
            return "succeeded"
        if task.status != "running":
            print(f"[{model_key}] 状态是 {task.status}，不补跑", flush=True)
            return task.status

        final_status = "running"
        async for event in service.run_task(task):
            if event.event not in {"status", "failed", "completed"}:
                continue
            text = event.data.get("text") or event.data.get("message") or event.data.get("url") or ""
            print(f"[{model_key}] {event.event} {text}", flush=True)
            if event.event == "completed":
                final_status = "succeeded"
            elif event.event == "failed":
                final_status = "failed"

        await session.refresh(task)
        print(f"[{model_key}] 结束状态 {task.status}", flush=True)
        return task.status or final_status


async def resume_one(task_id: uuid.UUID, model_key: str) -> str:
    print(f"开始补跑 {model_key}", flush=True)
    if not await _parent_html_ready(task_id):
        print(f"[{model_key}] 上一版 HTML 读不到，跳过", flush=True)
        return "no_parent_html"

    status = await _run_once(task_id, model_key)
    if status == "failed":
        print(f"[{model_key}] 第一次失败，再试一次", flush=True)
        await _reset_for_retry(task_id)
        status = await _run_once(task_id, model_key)
    return status


async def main() -> None:
    parser = argparse.ArgumentParser(description="补跑卡在 running 的生成任务")
    parser.add_argument("--batch-id", required=True, help="generation_batches.id")
    args = parser.parse_args()
    batch_id = uuid.UUID(args.batch_id)

    tasks = await _load_running_tasks(batch_id)
    if not tasks:
        print("没有需要补跑的任务", flush=True)
        return

    results = await asyncio.gather(*(resume_one(task_id, model_key) for task_id, model_key in tasks))
    print("补跑结果：", flush=True)
    for (_task_id, model_key), status in zip(tasks, results, strict=True):
        print(f"  {model_key}: {status}", flush=True)

    failed = [model_key for (_task_id, model_key), status in zip(tasks, results, strict=True) if status != "succeeded"]
    if failed:
        raise SystemExit(f"未完成：{', '.join(failed)}")


if __name__ == "__main__":
    asyncio.run(main())
