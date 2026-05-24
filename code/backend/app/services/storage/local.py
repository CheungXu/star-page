from __future__ import annotations

import asyncio
from pathlib import Path

from app.services.storage.base import StorageProvider


class LocalStorageProvider(StorageProvider):
    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)

    async def put_text(self, key: str, content: str, content_type: str = "text/html; charset=utf-8") -> None:
        path = self.base_dir / key
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, content, "utf-8")

    async def get_text(self, key: str) -> str:
        path = self.base_dir / key
        return await asyncio.to_thread(path.read_text, "utf-8")
