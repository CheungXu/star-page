from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SupplierBalance:
    """某个云/LLM 供应商账户的真实余额快照（对账用）。

    - configured：是否已配置可用凭据；未配置时仅作占位提示。
    - available_amount：可用余额（含现金+信用），单位为该供应商币种。
    - available_cash_amount：可用现金余额。
    - error：抓取失败时的错误信息（如权限不足、网络异常）。
    """

    vendor: str
    label: str
    configured: bool
    available_amount: float | None = None
    available_cash_amount: float | None = None
    currency: str | None = None
    fetched_at: datetime | None = None
    error: str | None = None
    note: str | None = None


class SupplierBalanceProvider:
    vendor: str = ""
    label: str = ""

    def is_configured(self) -> bool:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def fetch(self) -> SupplierBalance:  # pragma: no cover - 由子类实现
        """同步抓取余额（会被放进线程池执行）。"""
        raise NotImplementedError
