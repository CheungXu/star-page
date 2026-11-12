"""测试辅助：外部调用发生时，调用方的数据库会话不能还停在事务里。"""

from __future__ import annotations

import asyncio
import gc
import inspect

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.generation_service import GenerationService


def assert_external_call_idle(*, required: bool) -> bool:
    """找到调用方会话时确认它不在事务中。返回是否找到了会话。"""
    found = _find_caller_session()
    if found is None:
        if required:
            raise AssertionError("外部调用时找不到数据库会话，无法确认事务已结束")
        return False
    session, where = found
    assert not session.in_transaction(), f"{where} 调用外部时数据库事务仍未结束"
    return True


def _find_caller_session() -> tuple[AsyncSession, str] | None:
    for info in inspect.stack():
        found = _session_in_locals(info.frame.f_locals)
        if found is not None:
            return found, info.function

    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is current:
            continue
        found = _walk_awaitables(task.get_coro(), current, set())
        if found is not None:
            return found
    return None


def _walk_awaitables(obj, current, seen: set[int]):
    """沿协程等待链往下找。run_task 是异步生成器，中间会隔着 asend，要用引用把它接上。"""
    while obj is not None and id(obj) not in seen:
        seen.add(id(obj))
        frame = getattr(obj, "cr_frame", None) or getattr(obj, "ag_frame", None)
        if frame is not None:
            local = frame.f_locals
            waiter = local.get("waiter")
            owner = local.get("self")
            if waiter is current and isinstance(owner, GenerationService):
                return owner.session, frame.f_code.co_name
        if type(obj).__name__ == "async_generator_asend":
            for ref in gc.get_referents(obj):
                if hasattr(ref, "ag_frame"):
                    found = _walk_awaitables(ref, current, seen)
                    if found is not None:
                        return found
            return None
        obj = getattr(obj, "cr_await", None) or getattr(obj, "ag_await", None)
    return None


def _session_in_locals(local: dict) -> AsyncSession | None:
    session = local.get("session")
    if isinstance(session, AsyncSession):
        return session
    owner = local.get("self")
    if isinstance(owner, GenerationService):
        return owner.session
    return None
