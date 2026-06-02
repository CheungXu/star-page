from __future__ import annotations

from app.core.config import get_settings
from app.services.sms.base import SmsProvider
from app.services.sms.mock import MockSmsProvider


def create_sms_provider() -> SmsProvider:
    settings = get_settings()
    provider = settings.sms_provider.strip().lower()
    if provider == "aliyun":
        from app.services.sms.aliyun import AliyunSmsProvider

        return AliyunSmsProvider(settings)
    if provider == "mock":
        return MockSmsProvider()
    raise ValueError(f"不支持的短信服务商：{settings.sms_provider}")
