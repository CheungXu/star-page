"""web 与 worker 进程之间的生成通知，基于 PostgreSQL LISTEN/NOTIFY。

- 三个频道：派发新批次（web → worker）、停止任务（web → worker）、批次有新事件（worker/web → 所有 web）。
- 收：每个进程一条 asyncpg 专用连接，不占连接池；定期 SELECT 1 探活，失败就断开重连。
  连接断开期间 healthy 为 False：web 的事件流改为定时读库，worker 靠定时扫描补上漏掉的派发和停止。
- 发：走连接池里的普通会话，不依赖本进程的监听连接是否健康。通知在提交时送达，读到通知时数据已经可见。
- 通知只是提速，丢了不影响正确性：任务状态、停止标记、事件都在库里，扫描和轮询会补上。
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings

CHANNEL_DISPATCH = "sp_gen_dispatch"
CHANNEL_CANCEL = "sp_gen_cancel"
CHANNEL_BATCH = "sp_gen_batch"

# 同一个批次 200 毫秒内的多次事件只发一次通知；订阅方被唤醒后读库，一次就能拿到这段时间的全部事件。
BATCH_NOTIFY_DEBOUNCE_SECONDS = 0.2
RECONNECT_MAX_SECONDS = 30.0

Handler = Callable[[str], None]


async def send_in_session(session: AsyncSession, channel: str, payload: str) -> None:
    """在调用方的事务里发通知，随调用方的提交一起送达。调用方负责提交。"""
    await session.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": channel, "payload": payload})


def _session_factory() -> Callable[[], AsyncSession]:
    from app.core.database import AsyncSessionLocal

    return AsyncSessionLocal


async def send(channel: str, payload: str, session_factory: Callable[[], AsyncSession] | None = None) -> None:
    """单独开一个短事务发通知。发送失败只记日志：扫描与轮询会兜底。"""
    factory = session_factory or _session_factory()
    try:
        async with factory() as session:
            await send_in_session(session, channel, payload)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[bus] 发送通知失败 channel={channel} payload={payload}: {exc!r}", flush=True)


def listener_dsn() -> str:
    return get_settings().async_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class GenerationBus:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        session_factory: Callable[[], AsyncSession] | None = None,
        ping_seconds: float | None = None,
        ping_timeout_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        self._dsn = dsn
        self._session_factory = session_factory
        self._ping_seconds = ping_seconds if ping_seconds is not None else settings.generation_bus_ping_seconds
        self._ping_timeout = (
            ping_timeout_seconds if ping_timeout_seconds is not None else settings.generation_bus_ping_timeout_seconds
        )
        self._handlers: dict[str, list[Handler]] = {}
        self._on_connect: list[Callable[[], None]] = []
        self._task: asyncio.Task[None] | None = None
        self._conn: asyncpg.Connection | None = None
        self._stopping = False
        self._pending_batch: set[str] = set()
        self._background: set[asyncio.Task[None]] = set()
        self.healthy = False

    # ---- 订阅 ----

    def subscribe(self, channel: str, handler: Handler) -> None:
        """handler 在事件循环里同步调用，要做的异步工作自己 create_task。必须在 start 之前登记。"""
        self._handlers.setdefault(channel, []).append(handler)

    def on_connect(self, callback: Callable[[], None]) -> None:
        """每次（重新）连上后调用。断线期间的通知已经丢了，回调里应当把可能漏掉的状态补读一遍。"""
        self._on_connect.append(callback)

    async def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="generation-bus")

    async def stop(self) -> None:
        self._stopping = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self._close()
        for pending in list(self._background):
            pending.cancel()

    async def drop_connection(self) -> None:
        """测试用：模拟监听连接被中途掐断。"""
        conn = self._conn
        if conn is not None:
            conn.terminate()

    # ---- 发 ----

    async def send(self, channel: str, payload: str) -> None:
        await send(channel, payload, self._session_factory)

    def send_batch_soon(self, key: uuid.UUID | str) -> None:
        """批次有新事件：合并 200 毫秒内的重复通知后再发。"""
        payload = str(key)
        if payload in self._pending_batch or self._stopping:
            return
        self._pending_batch.add(payload)
        asyncio.get_running_loop().call_later(BATCH_NOTIFY_DEBOUNCE_SECONDS, self._flush_batch, payload)

    def _flush_batch(self, payload: str) -> None:
        # 先移出待发集合再发：发送期间到来的新事件会另排一次，不会被这次吞掉。
        self._pending_batch.discard(payload)
        self._spawn(self.send(CHANNEL_BATCH, payload))

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # ---- 收 ----

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stopping:
            try:
                conn = await asyncpg.connect(self._dsn or listener_dsn(), timeout=10)
                self._conn = conn
                for channel in self._handlers:
                    await conn.add_listener(channel, self._on_notify)
                self.healthy = True
                backoff = 1.0
                for callback in self._on_connect:
                    self._safe_call(callback)
                while not self._stopping:
                    await asyncio.sleep(self._ping_seconds)
                    await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=self._ping_timeout)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if not self._stopping:
                    print(f"[bus] 监听连接断开，{backoff:.0f} 秒后重连：{exc!r}", flush=True)
            finally:
                self.healthy = False
                await self._close()
            if self._stopping:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)

    async def _close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            await asyncio.wait_for(conn.close(), timeout=2)
        except Exception:  # noqa: BLE001 - 已经断开的连接直接丢掉
            conn.terminate()

    def _on_notify(self, _conn: object, _pid: int, channel: str, payload: str) -> None:
        for handler in self._handlers.get(channel, []):
            self._safe_call(lambda handler=handler: handler(payload))

    @staticmethod
    def _safe_call(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as exc:  # noqa: BLE001 - 单个处理函数出错不能拖垮监听
            print(f"[bus] 处理通知失败：{exc!r}", flush=True)
