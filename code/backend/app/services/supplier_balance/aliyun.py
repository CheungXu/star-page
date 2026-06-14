from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.services.supplier_balance.base import SupplierBalance, SupplierBalanceProvider

# BSS（费用中心）OpenAPI 的中国站接入点；账户余额是账号级，无需 region。
_BSS_ENDPOINT = "business.aliyuncs.com"


def _resolve_keys() -> tuple[str, str]:
    """优先用费用查询专用 AccessKey（ALIYUN_BILLING_*），回退到 OSS / 通用 ALIBABA_CLOUD_*。"""
    kid = (
        os.environ.get("ALIYUN_BILLING_ACCESS_KEY_ID")
        or os.environ.get("OBJECT_STORAGE_ACCESS_KEY_ID")
        or os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID")
        or ""
    ).strip()
    secret = (
        os.environ.get("ALIYUN_BILLING_ACCESS_KEY_SECRET")
        or os.environ.get("OBJECT_STORAGE_ACCESS_KEY_SECRET")
        or os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
        or ""
    ).strip()
    return kid, secret


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        # 阿里云返回形如 "1,234.56"，去掉千分位
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


class AliyunBalanceProvider(SupplierBalanceProvider):
    vendor = "aliyun"
    label = "阿里云（含百炼 Qwen）"

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
                note="未找到阿里云 AccessKey（OBJECT_STORAGE_ACCESS_KEY_ID/SECRET）",
            )

        from alibabacloud_bssopenapi20171214.client import Client
        from alibabacloud_tea_openapi import models as open_api_models

        config = open_api_models.Config(access_key_id=kid, access_key_secret=secret)
        config.endpoint = _BSS_ENDPOINT
        try:
            client = Client(config)
            resp = client.query_account_balance()
            body = resp.body
            if not getattr(body, "success", False):
                return SupplierBalance(
                    vendor=self.vendor,
                    label=self.label,
                    configured=True,
                    fetched_at=datetime.now(UTC),
                    error=f"{getattr(body, 'code', '')} {getattr(body, 'message', '')}".strip() or "查询失败",
                )
            data = body.data
            return SupplierBalance(
                vendor=self.vendor,
                label=self.label,
                configured=True,
                available_amount=_to_float(getattr(data, "available_amount", None)),
                available_cash_amount=_to_float(getattr(data, "available_cash_amount", None)),
                currency=getattr(data, "currency", None),
                fetched_at=datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001 - 第三方异常类型多样，统一兜底为可展示错误
            msg = str(exc)
            if "NotAuthorized" in msg:
                msg = "该 AccessKey 未授权 BSS 费用接口，请在 RAM 中授予 AliyunBSSReadOnlyAccess 后重试"
            return SupplierBalance(
                vendor=self.vendor,
                label=self.label,
                configured=True,
                fetched_at=datetime.now(UTC),
                error=msg[:300],
            )


@dataclass(frozen=True)
class AliyunBillItem:
    product_name: str
    product_code: str
    amount: float
    is_llm: bool


@dataclass(frozen=True)
class AliyunBillOverview:
    configured: bool
    billing_cycle: str | None = None
    items: list[AliyunBillItem] = field(default_factory=list)
    llm_total: float = 0.0
    infra_total: float = 0.0
    total: float = 0.0
    currency: str | None = None
    # 付款拆解（用于现金口径与代金券补贴透明化）
    gross_total: float = 0.0  # 原价合计（折扣前）
    coupon_deducted: float = 0.0  # 代金券+优惠券抵扣（免费补贴）
    prepaid_card_deducted: float = 0.0  # 储值卡/预付卡抵扣（预付费现金来源）
    payment_total: float = 0.0  # 现金支付合计（账单期内再掏现金）
    error: str | None = None
    note: str | None = None


def _is_llm_product(name: str, code: str, keywords: list[str]) -> bool:
    blob = f"{name} {code}".lower()
    return any(kw.lower() in blob for kw in keywords if kw)


def fetch_aliyun_bill_overview(billing_cycle: str, llm_keywords: list[str]) -> AliyunBillOverview:
    """拉取某账期（YYYY-MM）阿里云账单总览，按产品聚合并拆分 LLM/基础设施。

    成本口径取 `pretax_amount`（折扣抵扣后、不含税应付）。百炼等 LLM 产品标记 is_llm，
    其消费已按次计入 6001，不应重复入账；其余视为基础设施成本。
    """
    kid, secret = _resolve_keys()
    if not (kid and secret):
        return AliyunBillOverview(
            configured=False,
            billing_cycle=billing_cycle,
            note="未配置阿里云费用 AccessKey（ALIYUN_BILLING_ACCESS_KEY_ID/SECRET）",
        )

    from alibabacloud_bssopenapi20171214 import models as bss_models
    from alibabacloud_bssopenapi20171214.client import Client
    from alibabacloud_tea_openapi import models as open_api_models

    config = open_api_models.Config(access_key_id=kid, access_key_secret=secret)
    config.endpoint = _BSS_ENDPOINT
    try:
        client = Client(config)
        request = bss_models.QueryBillOverviewRequest(billing_cycle=billing_cycle)
        resp = client.query_bill_overview(request)
        body = resp.body
        if not getattr(body, "success", False):
            return AliyunBillOverview(
                configured=True,
                billing_cycle=billing_cycle,
                error=f"{getattr(body, 'code', '')} {getattr(body, 'message', '')}".strip() or "查询失败",
            )
        data = body.data
        raw_items = getattr(getattr(data, "items", None), "item", None) or []
        # 按产品名聚合多条（按量/包年包月会分行）
        agg: dict[str, AliyunBillItem] = {}
        currency: str | None = None
        gross = coupon = prepaid = payment = 0.0
        for it in raw_items:
            name = getattr(it, "product_name", None) or getattr(it, "product_code", None) or "未知产品"
            code = getattr(it, "product_code", None) or ""
            amount = float(getattr(it, "pretax_amount", 0) or 0)
            currency = currency or getattr(it, "currency", None)
            gross += float(getattr(it, "pretax_gross_amount", 0) or 0)
            coupon += float(getattr(it, "deducted_by_coupons", 0) or 0) + float(
                getattr(it, "deducted_by_cash_coupons", 0) or 0
            )
            prepaid += float(getattr(it, "deducted_by_prepaid_card", 0) or 0)
            payment += float(getattr(it, "payment_amount", 0) or 0)
            is_llm = _is_llm_product(name, code, llm_keywords)
            if name in agg:
                prev = agg[name]
                agg[name] = AliyunBillItem(name, code, prev.amount + amount, prev.is_llm or is_llm)
            else:
                agg[name] = AliyunBillItem(name, code, amount, is_llm)
        items = sorted(agg.values(), key=lambda x: x.amount, reverse=True)
        llm_total = round(sum(i.amount for i in items if i.is_llm), 2)
        infra_total = round(sum(i.amount for i in items if not i.is_llm), 2)
        return AliyunBillOverview(
            configured=True,
            billing_cycle=getattr(data, "billing_cycle", None) or billing_cycle,
            items=items,
            llm_total=llm_total,
            infra_total=infra_total,
            total=round(llm_total + infra_total, 2),
            currency=currency or "CNY",
            gross_total=round(gross, 2),
            coupon_deducted=round(coupon, 2),
            prepaid_card_deducted=round(prepaid, 2),
            payment_total=round(payment, 2),
        )
    except Exception as exc:  # noqa: BLE001 - 统一兜底为可展示错误
        msg = str(exc)
        if "NotAuthorized" in msg:
            msg = "该 AccessKey 未授权 BSS 费用接口，请在 RAM 中授予 AliyunBSSReadOnlyAccess 后重试"
        return AliyunBillOverview(
            configured=True,
            billing_cycle=billing_cycle,
            error=msg[:300],
        )
