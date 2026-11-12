from __future__ import annotations

import asyncio

import oss2

from app.services.storage.base import StorageProvider

# 同一进程的所有 OSS 调用共用一个连接池，复用到 OSS 的 TCP/TLS 连接。要不小于线程池大小，否则并发时多出的连接用完即丢。
OSS_CONNECTION_POOL_SIZE = 64


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

    async def get_bytes(self, key: str) -> bytes:
        # get_object 只读完响应头就返回，正文要在同一个线程里读完，不能回到事件循环里读网络。
        return await asyncio.to_thread(self._read_object, key)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.bucket.delete_object, key)

    def _read_object(self, key: str) -> bytes:
        return self.bucket.get_object(key).read()
