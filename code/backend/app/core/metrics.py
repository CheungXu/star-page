"""每隔一段时间往日志写一行 [metrics]，排查卡顿时先看这里：

    journalctl -u 'star-page-*' -g '\\[metrics\\]' --since -1h

- loop_lag_*：事件循环每秒醒一次，实际晚醒了多少毫秒。正常个位数；几十上百说明有同步代码卡住了事件循环。
- io_*：默认线程池已起的线程数和排队数。queue 长期不为 0 说明线程不够或下游（OSS、数据库）慢。
- parse_waiting / parse_procs / parse_last_rss_mb / parse_last_ms / parse_timeouts / parse_ooms：worker 文档解析子进程排队数、在跑数、最近子进程峰值内存、耗时及超时/OOM 次数。
- render_check_waiting / render_check_procs / render_check_last_rss_mb / render_check_last_ms / render_check_timeouts / render_check_skipped / render_check_issues：发布前无头浏览器渲染检查子进程排队数、在跑数、最近峰值内存、耗时及超时/跳过/问题计数。
- db_*：连接池借出数和溢出数，逼近上限时请求会等连接。
- rss_mb、open_fds：内存和打开的文件描述符（每条事件流、外部请求都占一个）。
- 其余是各角色自己报的业务量（事件流订阅数、页面缓存、在跑生成数等）。
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from app.core.executors import executor_load

TICK_SECONDS = 1.0


def _process_stats() -> dict[str, Any]:
    stats: dict[str, Any] = {}
    try:
        with open("/proc/self/statm") as handle:
            rss_pages = int(handle.read().split()[1])
        stats["rss_mb"] = round(rss_pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024)
        stats["open_fds"] = len(os.listdir("/proc/self/fd"))
    except (OSError, ValueError, IndexError):
        pass
    return stats


def _db_stats() -> dict[str, Any]:
    try:
        from app.core.database import engine

        pool = engine.sync_engine.pool
        return {"db_out": pool.checkedout(), "db_overflow": max(0, pool.overflow())}
    except Exception:  # noqa: BLE001 - 监控拿不到就不报
        return {}


def format_metrics(values: dict[str, Any]) -> str:
    return "[metrics] " + " ".join(f"{key}={value}" for key, value in values.items())


async def run_metrics_loop(role: str, interval: float, extra: Callable[[], dict[str, Any]] | None = None) -> None:
    if interval <= 0:
        return
    loop = asyncio.get_running_loop()
    lags: list[float] = []
    next_report = loop.time() + interval
    while True:
        started = loop.time()
        await asyncio.sleep(TICK_SECONDS)
        lags.append(max(0.0, loop.time() - started - TICK_SECONDS) * 1000)
        if loop.time() < next_report:
            continue
        next_report = loop.time() + interval
        values: dict[str, Any] = {"role": role, "pid": os.getpid()}
        if lags:
            values["loop_lag_avg_ms"] = round(sum(lags) / len(lags), 1)
            values["loop_lag_max_ms"] = round(max(lags), 1)
        lags.clear()
        values.update(executor_load())
        values.update(_db_stats())
        values.update(_process_stats())
        if extra is not None:
            try:
                values.update(extra())
            except Exception as exc:  # noqa: BLE001
                values["extra_error"] = type(exc).__name__
        print(format_metrics(values), flush=True)


def start_metrics(role: str, interval: float, extra: Callable[[], dict[str, Any]] | None = None) -> asyncio.Task[None] | None:
    if interval <= 0:
        return None
    return asyncio.create_task(run_metrics_loop(role, interval, extra), name="metrics")
