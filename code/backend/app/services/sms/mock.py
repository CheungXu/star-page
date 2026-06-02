from __future__ import annotations

from app.services.sms.base import SmsProvider, SmsSendResult


class MockSmsProvider(SmsProvider):
    name = "mock"

    async def send_verification_code(self, *, phone: str, code: str) -> SmsSendResult:
        print(f"短信验证码（mock）：phone={phone}, code={code}")
        return SmsSendResult(provider=self.name, code="OK", message="mock sent")
