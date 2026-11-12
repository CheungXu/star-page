"""生成页 HTML 原文的进程内缓存：打开 /p/、/u/ 时不必每次都从对象存储下载整页。

按存储键缓存。每个页面版本的存储键里带版本 id，写入后不再改动，所以缓存不会读到旧内容，也不需要跨进程失效。
删除、下架、权限、重新发布、改分享标题都在读缓存之前查库判断，不受缓存影响。
同一个键同时有很多请求时只下载一次，其余的等同一个结果（热门链接刚发进群时不会一下打出几百次下载）。
"""
from __future__ import annotations

import asyncio
import sys
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from app.core.config import get_settings
from app.services.storage.factory import create_storage_provider


async def _download(key: str) -> str:
    return await create_storage_provider().get_text(key)


class PageSourceCache:
    def __init__(self, max_bytes: int | None = None, loader: Callable[[str], Awaitable[str]] = _download) -> None:
        self._max_bytes = max_bytes
        self._loader = loader
        self._items: OrderedDict[str, tuple[str, int]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[str]] = {}
        self.bytes = 0
        self.hits = 0
        self.misses = 0

    @property
    def max_bytes(self) -> int:
        return self._max_bytes if self._max_bytes is not None else get_settings().page_html_cache_max_bytes

    def __len__(self) -> int:
        return len(self._items)

    async def get(self, key: str) -> str:
        cached = self._items.get(key)
        if cached is not None:
            self._items.move_to_end(key)
            self.hits += 1
            return cached[0]
        self.misses += 1
        pending = self._inflight.get(key)
        if pending is None:
            pending = asyncio.ensure_future(self._load(key))
            self._inflight[key] = pending
            pending.add_done_callback(lambda done, key=key: self._settle(key, done))
        # 等待者断开不能取消共用的下载，其他人还在等。
        return await asyncio.shield(pending)

    def clear(self) -> None:
        self._items.clear()
        self.bytes = 0

    def _settle(self, key: str, done: asyncio.Future[str]) -> None:
        self._inflight.pop(key, None)
        if not done.cancelled():
            done.exception()  # 等待者都断开时也取走异常，免得事件循环报未读取

    async def _load(self, key: str) -> str:
        text = await self._loader(key)
        self._put(key, text)
        return text

    def _put(self, key: str, text: str) -> None:
        limit = self.max_bytes
        size = sys.getsizeof(text)
        # 单页超过上限的八分之一不缓存，免得一页挤掉一大片。
        if limit <= 0 or size > limit // 8:
            return
        previous = self._items.pop(key, None)
        if previous is not None:
            self.bytes -= previous[1]
        self._items[key] = (text, size)
        self.bytes += size
        while self.bytes > limit and self._items:
            _key, (_text, evicted) = self._items.popitem(last=False)
            self.bytes -= evicted


page_source_cache = PageSourceCache()
