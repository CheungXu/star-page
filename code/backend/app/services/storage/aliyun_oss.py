from __future__ import annotations

import asyncio
from pathlib import Path
from typing import BinaryIO

import oss2

from app.services.storage.base import StorageProvider

# 同一进程的所有 OSS 调用共用一个连接池，复用到 OSS 的 TCP/TLS 连接。要不小于线程池大小，否则并发时多出的连接用完即丢。
OSS_CONNECTION_POOL_SIZE = 64
_STREAM_CHUNK_BYTES = 1024 * 1024


class AliyunOssStorageProvider(StorageProvider):
    def __init__(self, bucket: str, endpoint: str, access_key_id: str, access_key_secret: str) -> None:
        if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
            endpoint = f"https://{endpoint}"

        auth = oss2.Auth(access_key_id, access_key_secret)
        self.bucket = oss2.Bucket(auth, endpoint, bucket, session=oss2.Session(pool_size=OSS_CONNECTION_POOL_SIZE))

    async def put_text(self, key: str, content: str, content_type: str = "text/html; charset=utf-8") -> None:
        headers = {"Content-Type": content_type}
        await asyncio.to_thread(self.bucket.put_object, key, content.encode("utf-8"), headers=headers)

    async def get_text(self, key: str) -> str:
        data = await self.get_bytes(key)
        return data.decode("utf-8")

    async def put_bytes(self, key: str, content: bytes, content_type: str) -> None:
        headers = {"Content-Type": content_type}
        await asyncio.to_thread(self.bucket.put_object, key, content, headers=headers)

    async def put_stream(self, key: str, stream: BinaryIO, content_type: str) -> None:
        headers = {"Content-Type": content_type}
        await asyncio.to_thread(self._put_stream_sync, key, stream, headers)

    async def get_bytes(self, key: str) -> bytes:
        # get_object 只读完响应头就返回，正文要在同一个线程里读完，不能回到事件循环里读网络。
        return await asyncio.to_thread(self._read_object, key)

    async def download_file(self, key: str, destination: Path) -> None:
        await asyncio.to_thread(self._download_object, key, destination)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.bucket.delete_object, key)

    def _put_stream_sync(self, key: str, stream: BinaryIO, headers: dict[str, str]) -> None:
        stream.seek(0)
        self.bucket.put_object(key, stream, headers=headers)

    def _read_object(self, key: str) -> bytes:
        body = self.bucket.get_object(key)
        try:
            return body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()

    def _download_object(self, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        body = self.bucket.get_object(key)
        try:
            with destination.open("wb") as handle:
                while True:
                    chunk = body.read(_STREAM_CHUNK_BYTES) if hasattr(body, "read") else b""
                    if not chunk:
                        break
                    handle.write(chunk)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
