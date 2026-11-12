from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO


class StorageProvider(ABC):
    @abstractmethod
    async def put_text(self, key: str, content: str, content_type: str = "text/html; charset=utf-8") -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_text(self, key: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def put_bytes(self, key: str, content: bytes, content_type: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def put_stream(self, key: str, stream: BinaryIO, content_type: str) -> None:
        """流式上传文件对象；子类应直接流式写出，避免在内存里拼整份字节。"""
        stream.seek(0)
        await self.put_bytes(key, stream.read(), content_type)

    async def download_file(self, key: str, destination: Path) -> None:
        """流式下载对象到本地路径；子类应按块落盘，避免在父进程内存里驻留整份字节。"""
        data = await self.get_bytes(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
