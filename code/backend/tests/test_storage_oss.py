"""OSS 读正文不在事件循环线程里；同一份配置复用同一个客户端（连接池）。"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.services.storage import factory  # noqa: E402
from app.services.storage.aliyun_oss import AliyunOssStorageProvider  # noqa: E402


class _Body:
    def __init__(self, threads: list[str]) -> None:
        self._threads = threads

    def read(self) -> bytes:
        self._threads.append(threading.current_thread().name)
        return b"<html></html>"


class _Bucket:
    def __init__(self, threads: list[str]) -> None:
        self._threads = threads

    def get_object(self, _key: str) -> _Body:
        return _Body(self._threads)


def test_get_bytes_reads_body_off_event_loop():
    provider = AliyunOssStorageProvider("bucket-name", "oss-cn-guangzhou.aliyuncs.com", "ak", "sk")
    threads: list[str] = []
    provider.bucket = _Bucket(threads)

    async def run() -> tuple[bytes, str]:
        data = await provider.get_bytes("pages/x/index.html")
        return data, threading.current_thread().name

    data, loop_thread = asyncio.run(run())
    assert data == b"<html></html>"
    assert threads and threads[0] != loop_thread


def test_factory_reuses_oss_client(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_provider", "aliyun")
    monkeypatch.setattr(settings, "object_storage_bucket", "bucket-name")
    monkeypatch.setattr(settings, "object_storage_endpoint", "oss-cn-guangzhou.aliyuncs.com")
    monkeypatch.setattr(settings, "object_storage_access_key_id", "ak")
    monkeypatch.setattr(settings, "object_storage_access_key_secret", "sk")
    monkeypatch.setattr(factory, "_oss_providers", {})
    first = factory.create_storage_provider()
    assert factory.create_storage_provider() is first
    monkeypatch.setattr(settings, "object_storage_bucket", "other-bucket")
    assert factory.create_storage_provider() is not first
