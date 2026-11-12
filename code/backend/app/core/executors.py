"""进程内的线程池：默认池给 asyncio.to_thread 用；文档完整解析已改为独立受限子进程。"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from app.core.config import get_settings

_parse_executor: ThreadPoolExecutor | None = None
_default_executor: ThreadPoolExecutor | None = None


def install_default_executor() -> None:
    """在事件循环启动后、处理任何请求之前调用。"""
    global _default_executor
    size = max(4, get_settings().thread_pool_size)
    _default_executor = ThreadPoolExecutor(max_workers=size, thread_name_prefix="sp-io")
    asyncio.get_running_loop().set_default_executor(_default_executor)


def executor_load() -> dict[str, int]:
    """监控用：线程池已起的线程数和排队的任务数。"""
    load: dict[str, int] = {}
    for name, pool in (("io", _default_executor), ("parse", _parse_executor)):
        if pool is not None:
            load[f"{name}_threads"] = len(pool._threads)
            load[f"{name}_queue"] = pool._work_queue.qsize()
    return load


def parse_executor() -> ThreadPoolExecutor:
    global _parse_executor
    if _parse_executor is None:
        size = max(1, get_settings().document_parse_concurrency)
        _parse_executor = ThreadPoolExecutor(max_workers=size, thread_name_prefix="sp-parse")
    return _parse_executor
