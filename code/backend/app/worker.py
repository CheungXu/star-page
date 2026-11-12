"""生成 worker 进程入口：python -m app.worker（APP_ROLE=worker）。

只跑生成，不对外提供 HTTP。线上只能开一个：名额闸门和任务登记表都在这个进程的内存里。
启动顺序：扫尾上一个进程留下的任务 → 开始监听派发与停止通知 → 定时兜底扫描。
收到 SIGTERM / SIGINT 时只取消在跑的协程、不改任务状态，交给下次启动扫尾。
"""
from __future__ import annotations

import asyncio
import signal

from app.core.config import get_settings
from app.core.database import engine
from app.services.generation_bus import CHANNEL_CANCEL, CHANNEL_DISPATCH, GenerationBus
from app.services.generation_runner import generation_runner


async def run_worker() -> None:
    settings = get_settings()
    if settings.app_role != "worker":
        raise SystemExit("worker 进程必须设置 APP_ROLE=worker")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    if settings.generation_startup_sweep:
        try:
            await generation_runner.startup_sweep()
        except Exception as exc:  # noqa: BLE001 - 扫尾失败不阻止启动，扫描仍会补上 pending 任务
            print(f"[worker] 启动扫尾失败：{exc!r}", flush=True)

    # 监听刚（重新）连上时立刻扫一次：断线期间的派发和停止通知已经丢了。
    rescan = asyncio.Event()
    bus = GenerationBus()
    bus.subscribe(CHANNEL_DISPATCH, generation_runner.handle_dispatch_notice)
    bus.subscribe(CHANNEL_CANCEL, generation_runner.handle_cancel_notice)
    bus.on_connect(rescan.set)
    generation_runner.attach_bus(bus)
    await bus.start()

    scan = asyncio.create_task(
        generation_runner.run_scan_loop(settings.generation_dispatch_scan_seconds, rescan), name="generation-scan"
    )
    print("[worker] 已启动", flush=True)
    try:
        await stop.wait()
    finally:
        print("[worker] 正在退出", flush=True)
        await generation_runner.shutdown()
        scan.cancel()
        await bus.stop()
        await engine.dispose()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
