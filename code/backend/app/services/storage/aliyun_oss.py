from __future__ import annotations

import asyncio

import oss2

from app.services.storage.base import StorageProvider


class AliyunOssStorageProvider(StorageProvider):
    def __init__(self, bucket: str, endpoint: str, access_key_id: str, access_key_secret: str) -> None:
        if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
            endpoint = f"https://{endpoint}"

        auth = oss2.Auth(access_key_id, access_key_secret)
        self.bucket = oss2.Bucket(auth, endpoint, bucket)

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
        result = await asyncio.to_thread(self.bucket.get_object, key)
        return result.read()

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.bucket.delete_object, key)
