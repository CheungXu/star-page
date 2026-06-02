from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SmsSendResult:
    provider: str
    request_id: str | None = None
    code: str | None = None
    message: str | None = None


class SmsProvider:
    name = "base"

    async def send_verification_code(self, *, phone: str, code: str) -> SmsSendResult:
        raise NotImplementedError
