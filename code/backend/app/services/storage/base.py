from __future__ import annotations

from abc import ABC, abstractmethod


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
