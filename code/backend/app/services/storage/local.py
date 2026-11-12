from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import BinaryIO

from app.services.storage.base import StorageProvider

_COPY_CHUNK_BYTES = 1024 * 1024


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

    async def put_bytes(self, key: str, content: bytes, content_type: str) -> None:
        del content_type
        path = self.base_dir / key
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, content)

    async def put_stream(self, key: str, stream: BinaryIO, content_type: str) -> None:
        del content_type
        path = self.base_dir / key
        await asyncio.to_thread(self._write_stream, path, stream)

    async def get_bytes(self, key: str) -> bytes:
        path = self.base_dir / key
        return await asyncio.to_thread(path.read_bytes)

    async def download_file(self, key: str, destination: Path) -> None:
        source = self.base_dir / key
        await asyncio.to_thread(self._copy_file, source, destination)

    async def delete(self, key: str) -> None:
        path = self.base_dir / key
        await asyncio.to_thread(path.unlink, True)

    @staticmethod
    def _write_stream(path: Path, stream: BinaryIO) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream.seek(0)
        with path.open("wb") as handle:
            shutil.copyfileobj(stream, handle, length=_COPY_CHUNK_BYTES)

    @staticmethod
    def _copy_file(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as src, destination.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=_COPY_CHUNK_BYTES)
