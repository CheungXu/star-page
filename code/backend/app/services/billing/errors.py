from __future__ import annotations


class BillingError(Exception):
    """计费相关业务异常基类。code 供 API 层映射 HTTP 状态。"""

    code = "billing_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InsufficientCreditsError(BillingError):
    """积分不足，需充值。API 层映射 402。"""

    code = "insufficient_credits"


class AnonLimitError(BillingError):
    """匿名免费额度用尽，需登录/注册。API 层映射 402 + need_login。"""

    code = "anon_limit_reached"


class ModelNotAllowedError(BillingError):
    """匿名用户选择了不允许的模型或超出可选数量。API 层映射 403 + need_login。"""

    code = "model_not_allowed_for_anon"
