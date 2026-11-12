"""生成 worker 进程入口：python -m app.worker（APP_ROLE=worker）。

只跑生成，不对外提供 HTTP。可以开多个：名额按库里 running 的任务数算，认领排着做；每个任务记着是哪个 worker 在跑。
启动顺序：接手失联任务 → 开始监听派发与停止通知 → 定时续期、兜底扫描。
收到 SIGTERM / SIGINT 时先排空：不再领新任务，手上的跑完再退出（最多 WORKER_DRAIN_TIMEOUT_SECONDS）。
排空期间再收到一次信号就不等了：剩下的任务交给其他 worker 或下次启动接手。
"""
from __future__ import annotations

import asyncio
import signal

from app.core.config import get_settings
from app.core.database import engine
from app.core.executors import install_default_executor
from app.core.metrics import start_metrics
from app.services.document_parser.pipeline import active_parse_jobs, hand_over_on_exit
from app.services.generation_bus import CHANNEL_CANCEL, CHANNEL_DISPATCH, GenerationBus
from app.services.generation_runner import generation_runner


async def run_worker() -> None:
    settings = get_settings()
    if settings.app_role != "worker":
        raise SystemExit("worker 进程必须设置 APP_ROLE=worker")

    install_default_executor()
    stop = asyncio.Event()
    force = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_signal() -> None:
        if stop.is_set():
            force.set()
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, on_signal)

    if settings.generation_startup_sweep:
        try:
            await generation_runner.startup_sweep()
        except Exception as exc:  # noqa: BLE001 - 扫尾失败不阻止启动，扫描仍会补上
            print(f"[worker] 启动扫尾失败：{exc!r}", flush=True)

    # 监听刚（重新）连上时立刻扫一次：断线期间的派发和停止通知已经丢了。
    rescan = asyncio.Event()
    bus = GenerationBus()
    bus.subscribe(CHANNEL_DISPATCH, generation_runner.handle_dispatch_notice)
    bus.subscribe(CHANNEL_CANCEL, generation_runner.handle_cancel_notice)
    bus.on_connect(rescan.set)
    generation_runner.attach_bus(bus)
    await bus.start()

    background = [
        asyncio.create_task(
            generation_runner.run_scan_loop(settings.generation_dispatch_scan_seconds, rescan), name="generation-scan"
        ),
        asyncio.create_task(
            generation_runner.run_heartbeat_loop(settings.generation_heartbeat_seconds), name="generation-heartbeat"
        ),
    ]
    metrics = start_metrics(
        "worker",
        settings.metrics_interval_seconds,
        lambda: {**generation_runner.stats(), "parse_jobs": active_parse_jobs(), "bus": int(bus.healthy)},
    )
    if metrics is not None:
        background.append(metrics)
    generation_runner.kick()
    print(f"[worker] 已启动 worker_id={generation_runner.worker_id}", flush=True)
    try:
        await stop.wait()
        print("[worker] 收到退出信号，不再领新任务，等手上的跑完", flush=True)
        drain = asyncio.create_task(
            generation_runner.drain(settings.worker_drain_timeout_seconds, busy=lambda: active_parse_jobs() > 0)
        )
        forced = asyncio.create_task(force.wait())
        await asyncio.wait({drain, forced}, return_when=asyncio.FIRST_COMPLETED)
        forced.cancel()
        if drain.done() and drain.result():
            print("[worker] 已排空", flush=True)
        else:
            drain.cancel()
            print("[worker] 排空超时或被强制退出，剩下的任务交给其他 worker 接手", flush=True)
    finally:
        hand_over_on_exit()
        await generation_runner.shutdown()
        for task in background:
            task.cancel()
        await bus.stop()
        await engine.dispose()
        print("[worker] 已退出", flush=True)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
