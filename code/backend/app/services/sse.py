from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SseEvent:
    event: str
    data: dict[str, Any]
    # 有值时写成 SSE 的 id 字段；浏览器断线重连会把它放进 Last-Event-ID，用来续传。
    id: str | int | None = None


def format_sse(event: SseEvent) -> str:
    payload = json.dumps(event.data, ensure_ascii=False, default=str)
    head = f"id: {event.id}\n" if event.id is not None else ""
    return f"{head}event: {event.event}\ndata: {payload}\n\n"


def format_sse_comment(text: str) -> str:
    """SSE 注释行：浏览器忽略，只用来让反向代理和连接知道流还活着。"""
    return f": {text}\n\n"
