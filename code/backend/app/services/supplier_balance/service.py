from __future__ import annotations

import asyncio
import time

from app.services.supplier_balance.aliyun import AliyunBalanceProvider
from app.services.supplier_balance.base import SupplierBalance, SupplierBalanceProvider
from app.services.supplier_balance.volcengine import VolcengineBalanceProvider

# 余额接口有调用频控且变动不频繁，默认缓存 5 分钟；管理员可强制刷新。
_CACHE_TTL_SECONDS = 300

_PROVIDERS: list[SupplierBalanceProvider] = [
    AliyunBalanceProvider(),
    VolcengineBalanceProvider(),
]

# vendor -> (snapshot, monotonic_ts)
_cache: dict[str, tuple[SupplierBalance, float]] = {}


async def _fetch_one(provider: SupplierBalanceProvider, *, force: bool) -> SupplierBalance:
    now = time.monotonic()
    if not force:
        cached = _cache.get(provider.vendor)
        if cached and (now - cached[1]) < _CACHE_TTL_SECONDS:
            return cached[0]
    # 未配置的供应商不发起网络请求，直接返回占位状态（也缓存，避免重复构造）。
    if not provider.is_configured():
        snapshot = provider.fetch()
    else:
        snapshot = await asyncio.to_thread(provider.fetch)
    _cache[provider.vendor] = (snapshot, time.monotonic())
    return snapshot


async def get_supplier_balances(*, force: bool = False) -> list[SupplierBalance]:
    """并发抓取各供应商余额（带 TTL 缓存）。force=True 时绕过缓存强制刷新。"""
    return list(await asyncio.gather(*(_fetch_one(p, force=force) for p in _PROVIDERS)))
