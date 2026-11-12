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
    def __init__(self, threads: list[str], data: bytes = b"<html></html>", *, fail_after_first: bool = False) -> None:
        self._threads = threads
        self._data = data
        self._offset = 0
        self._fail_after_first = fail_after_first
        self.closed = False

    def read(self, amt: int | None = None) -> bytes:
        self._threads.append(threading.current_thread().name)
        if amt is None:
            out = self._data[self._offset :]
            self._offset = len(self._data)
            return out
        if self._offset > 0 and self._fail_after_first:
            raise RuntimeError("network broken")
        out = self._data[self._offset : self._offset + amt]
        self._offset += len(out)
        return out

    def close(self) -> None:
        self.closed = True


class _Bucket:
    def __init__(self, threads: list[str], *, data: bytes = b"<html></html>", fail_after_first: bool = False) -> None:
        self._threads = threads
        self._data = data
        self._fail_after_first = fail_after_first
        self.bodies: list[_Body] = []

    def get_object(self, _key: str) -> _Body:
        body = _Body(self._threads, self._data, fail_after_first=self._fail_after_first)
        self.bodies.append(body)
        return body


def test_get_bytes_reads_body_off_event_loop():
    provider = AliyunOssStorageProvider("bucket-name", "oss-cn-guangzhou.aliyuncs.com", "ak", "sk")
    threads: list[str] = []
    bucket = _Bucket(threads)
    provider.bucket = bucket

    async def run() -> tuple[bytes, str]:
        data = await provider.get_bytes("pages/x/index.html")
        return data, threading.current_thread().name

    data, loop_thread = asyncio.run(run())
    assert data == b"<html></html>"
    assert threads and threads[0] != loop_thread
    assert bucket.bodies[0].closed is True


def test_download_file_closes_body_on_success_and_error(tmp_path: Path):
    provider = AliyunOssStorageProvider("bucket-name", "oss-cn-guangzhou.aliyuncs.com", "ak", "sk")
    threads: list[str] = []
    payload = b"x" * (2 * 1024 * 1024 + 17)
    ok_bucket = _Bucket(threads, data=payload)
    provider.bucket = ok_bucket
    dest = tmp_path / "ok.bin"
    asyncio.run(provider.download_file("batch-uploads/a.bin", dest))
    assert dest.read_bytes() == payload
    assert ok_bucket.bodies[0].closed is True

    bad_bucket = _Bucket(threads, data=payload, fail_after_first=True)
    provider.bucket = bad_bucket
    try:
        asyncio.run(provider.download_file("batch-uploads/b.bin", tmp_path / "bad.bin"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")
    assert bad_bucket.bodies[0].closed is True


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
