from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from app.services.supplier_balance.base import SupplierBalance, SupplierBalanceProvider


def _resolve_keys() -> tuple[str, str]:
    """火山账户余额需要账号 IAM 的 AK/SK（不是 ARK 推理 key）。"""
    kid = (os.environ.get("VOLC_ACCESSKEY") or os.environ.get("VOLCENGINE_ACCESS_KEY") or "").strip()
    secret = (os.environ.get("VOLC_SECRETKEY") or os.environ.get("VOLCENGINE_SECRET_KEY") or "").strip()
    return kid, secret


def _first_float(payload: dict, *keys: str) -> float | None:
    for key in keys:
        if key in payload and payload[key] is not None:
            try:
                return float(str(payload[key]).replace(",", ""))
            except (ValueError, TypeError):
                continue
    return None


class VolcengineBalanceProvider(SupplierBalanceProvider):
    vendor = "volcengine"
    label = "火山引擎（豆包 Doubao）"

    def is_configured(self) -> bool:
        kid, secret = _resolve_keys()
        return bool(kid and secret)

    def fetch(self) -> SupplierBalance:
        kid, secret = _resolve_keys()
        if not (kid and secret):
            return SupplierBalance(
                vendor=self.vendor,
                label=self.label,
                configured=False,
                note="未配置火山账号 AK/SK（当前仅有 ARK 推理 key，无法查余额）。"
                "请在火山控制台创建带财务只读权限的 AK/SK，写入 VOLC_ACCESSKEY / VOLC_SECRETKEY。",
            )

        try:
            from volcengine.ApiInfo import ApiInfo
            from volcengine.billing.BillingService import BillingService

            svc = BillingService()
            svc.set_ak(kid)
            svc.set_sk(secret)
            # SDK 未内置查余额动作，这里注册 QueryBalanceAcct（财务 OpenAPI）。
            svc.api_info["QueryBalanceAcct"] = ApiInfo(
                "POST", "/", {"Action": "QueryBalanceAcct", "Version": "2022-01-01"}, {}, {}
            )
            raw = svc.json("QueryBalanceAcct", {}, json.dumps({}))
            body = json.loads(raw) if isinstance(raw, str) else raw
            result = (body or {}).get("Result") or {}
            available = _first_float(result, "AvailableBalance", "available_balance")
            cash = _first_float(result, "CashBalance", "cash_balance")
            return SupplierBalance(
                vendor=self.vendor,
                label=self.label,
                configured=True,
                available_amount=available,
                available_cash_amount=cash,
                currency=result.get("Currency") or "CNY",
                fetched_at=datetime.now(UTC),
                error=None if available is not None else f"返回未解析到余额字段：{str(result)[:160]}",
            )
        except Exception as exc:  # noqa: BLE001 - 统一兜底为可展示错误
            return SupplierBalance(
                vendor=self.vendor,
                label=self.label,
                configured=True,
                fetched_at=datetime.now(UTC),
                error=str(exc)[:300],
            )
