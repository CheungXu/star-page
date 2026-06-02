from __future__ import annotations

import json
import os
from typing import Any

from alibabacloud_credentials.client import Client as CredentialClient
from alibabacloud_dysmsapi20170525.client import Client as DysmsapiClient
from alibabacloud_dysmsapi20170525 import models as dysms_models
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models

from app.core.config import Settings
from app.services.sms.base import SmsProvider, SmsSendResult


class AliyunSmsProvider(SmsProvider):
    name = "aliyun"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.aliyun_sms_sign_name:
            raise ValueError("缺少 ALIYUN_SMS_SIGN_NAME")
        if not settings.aliyun_sms_template_code:
            raise ValueError("缺少 ALIYUN_SMS_TEMPLATE_CODE")

    def _create_client(self) -> DysmsapiClient:
        access_key_id = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "").strip()
        access_key_secret = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "").strip()
        if access_key_id and access_key_secret:
            config = open_api_models.Config(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
            )
        else:
            credential = CredentialClient()
            config = open_api_models.Config(credential=credential)
        config.endpoint = self.settings.aliyun_sms_endpoint
        return DysmsapiClient(config)

    async def send_verification_code(self, *, phone: str, code: str) -> SmsSendResult:
        client = self._create_client()
        request = dysms_models.SendSmsRequest(
            phone_numbers=phone,
            sign_name=self.settings.aliyun_sms_sign_name,
            template_code=self.settings.aliyun_sms_template_code,
            template_param=json.dumps({"code": code}, ensure_ascii=False),
        )

        response = client.send_sms_with_options(request, util_models.RuntimeOptions())
        body: Any = getattr(response, "body", None)
        result_code = getattr(body, "code", None)
        message = getattr(body, "message", None)
        request_id = getattr(body, "request_id", None)
        if result_code and result_code != "OK":
            raise RuntimeError(f"阿里云短信发送失败：{result_code} {message or ''}".strip())
        return SmsSendResult(provider=self.name, request_id=request_id, code=result_code, message=message)
