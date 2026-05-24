from __future__ import annotations

from abc import ABC, abstractmethod


class StorageProvider(ABC):
    @abstractmethod
    async def put_text(self, key: str, content: str, content_type: str = "text/html; charset=utf-8") -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_text(self, key: str) -> str:
        raise NotImplementedError
