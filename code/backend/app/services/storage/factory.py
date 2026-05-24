from __future__ import annotations

from app.core.config import get_settings
from app.services.storage.aliyun_oss import AliyunOssStorageProvider
from app.services.storage.base import StorageProvider
from app.services.storage.local import LocalStorageProvider


def create_storage_provider() -> StorageProvider:
    settings = get_settings()
    provider = settings.object_storage_provider.lower()

    if provider == "local":
        return LocalStorageProvider(settings.local_storage_dir)

    if provider == "aliyun":
        required = {
            "OBJECT_STORAGE_BUCKET": settings.object_storage_bucket,
            "OBJECT_STORAGE_ENDPOINT": settings.object_storage_endpoint,
            "OBJECT_STORAGE_ACCESS_KEY_ID": settings.object_storage_access_key_id,
            "OBJECT_STORAGE_ACCESS_KEY_SECRET": settings.object_storage_access_key_secret,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError(f"缺少 OSS 配置：{', '.join(missing)}")

        return AliyunOssStorageProvider(
            bucket=settings.object_storage_bucket,
            endpoint=settings.object_storage_endpoint,
            access_key_id=settings.object_storage_access_key_id,
            access_key_secret=settings.object_storage_access_key_secret,
        )

    raise ValueError(f"不支持的对象存储 Provider：{settings.object_storage_provider}")
