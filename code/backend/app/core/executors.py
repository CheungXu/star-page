"""进程内的线程池：默认池给 asyncio.to_thread 用，文档解析另用一个小池。"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from app.core.config import get_settings

_parse_executor: ThreadPoolExecutor | None = None


def install_default_executor() -> None:
    """在事件循环启动后、处理任何请求之前调用。"""
    size = max(4, get_settings().thread_pool_size)
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=size, thread_name_prefix="sp-io"))


def parse_executor() -> ThreadPoolExecutor:
    global _parse_executor
    if _parse_executor is None:
        size = max(1, get_settings().document_parse_threads)
        _parse_executor = ThreadPoolExecutor(max_workers=size, thread_name_prefix="sp-parse")
    return _parse_executor
