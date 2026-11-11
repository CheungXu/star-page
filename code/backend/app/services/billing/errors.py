from __future__ import annotations

from typing import Any


class BillingError(Exception):
    """计费相关业务异常基类。code 与 extra 供 API 层构造结构化响应。"""

    code = "billing_error"

    def __init__(self, message: str, *, code: str | None = None, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.extra: dict[str, Any] = dict(extra or {})

    def to_detail(self, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message, **self.extra}
        payload.update(overrides)
        return payload


class InsufficientCreditsError(BillingError):
    """积分不足、预授权不足或存在待补缴欠费，需充值或降级模型。API 层映射 402。"""

    code = "insufficient_preauth_credits"


class FlagshipUpgradeRequiredError(InsufficientCreditsError):
    """未充值用户首秀券已用完后选择了旗舰模型，需首充解锁或切换轻量模型。API 层映射 402。"""

    code = "flagship_requires_recharge"


class AnonLimitError(BillingError):
    """匿名免费额度用尽，需登录/注册。API 层映射 402 + need_login。"""

    code = "anon_limit_reached"


class ModelNotAllowedError(BillingError):
    """匿名用户选择了不允许的模型或超出可选数量。API 层映射 402 + need_login。"""

    code = "model_not_allowed_for_anon"
