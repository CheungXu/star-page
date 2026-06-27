from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class LedgerAccountStat(BaseModel):
    code: str
    name: str
    type: str
    debit: float
    credit: float
    net: float


class AdminOverview(BaseModel):
    # 第一行：付费业务（不含赠送）——代表真实付费现金业务的财务状况
    total_recharge_cny: float
    paid_revenue_cny: float
    paid_cogs_cny: float
    paid_gross_profit_cny: float
    paid_gross_margin: float | None

    # 第二行：赠送台账——赠送额度的记账
    gift_granted_cny: float
    gift_unused_cny: float
    gift_revenue_cny: float
    gift_cogs_cny: float
    trial_cogs_cny: float

    # 第三行：含赠送合计——把赠送确认收入与全部成本一并计入
    total_revenue_cny: float
    total_cogs_cny: float
    total_gross_profit_cny: float
    total_gross_margin: float | None

    # 期间费用与营业利润：基础设施(服务器等)成本、支付手续费，营业利润=综合毛利-基础设施-手续费
    infra_cost_cny: float
    payment_fee_cny: float
    operating_profit_cny: float

    # 其他：资产负债与全站余额
    deferred_revenue_cny: float
    receivable_third_party_cny: float
    prepaid_cloud_balance_cny: float
    prepaid_cloud_topup_cny: float
    total_paid_balance_credits: int
    total_gift_balance_credits: int
    user_count: int
    accounts: list[LedgerAccountStat]


class ModelMarkupItem(BaseModel):
    key: str
    label: str
    provider: str
    available: bool
    markup: float
    is_custom: bool
    pricing_summary: str | None = None


class ModelMarkupConfig(BaseModel):
    default_markup: float
    models: list[ModelMarkupItem]


class ModelMarkupUpdateRequest(BaseModel):
    default_markup: float
    model_markups: dict[str, float]


class SupplierTopupRequest(BaseModel):
    amount_cny: float
    memo: str | None = None


class SupplierTopupResponse(BaseModel):
    ok: bool
    prepaid_cloud_balance_cny: float
    prepaid_cloud_topup_cny: float


class SupplierBalanceItem(BaseModel):
    vendor: str
    label: str
    configured: bool
    available_amount: float | None = None
    available_cash_amount: float | None = None
    currency: str | None = None
    fetched_at: datetime | None = None
    error: str | None = None
    note: str | None = None


class AliyunBillProductItem(BaseModel):
    product_name: str
    product_code: str
    amount: float
    is_llm: bool


class AliyunBillOverviewResponse(BaseModel):
    configured: bool
    billing_cycle: str | None = None
    items: list[AliyunBillProductItem] = []
    llm_total: float = 0.0
    infra_total: float = 0.0
    total: float = 0.0
    currency: str | None = None
    # 付款拆解
    gross_total: float = 0.0
    coupon_deducted: float = 0.0
    prepaid_card_deducted: float = 0.0
    payment_total: float = 0.0
    # 入账状态
    posted: bool = False
    posted_infra_cny: float = 0.0
    # 百炼成本偏差对账：我们按次估算 vs 账单实际
    estimated_llm_cogs_cny: float = 0.0
    llm_actual_cny: float = 0.0
    llm_deviation_cny: float = 0.0
    llm_deviation_pct: float | None = None
    error: str | None = None
    note: str | None = None


class WechatSettlementRequest(BaseModel):
    settlement_cny: float
    fee_cny: float = 0.0
    memo: str | None = None


class WechatSettlementResponse(BaseModel):
    ok: bool
    receivable_third_party_cny: float


class WechatFundflowResponse(BaseModel):
    configured: bool
    bill_date: str
    settlement_cny: float = 0.0
    fee_cny: float = 0.0
    income_cny: float = 0.0
    row_count: int = 0
    unknown_types: list[str] = []
    posted: bool = False
    error: str | None = None
    note: str | None = None


class WechatFundflowPostRequest(BaseModel):
    bill_date: str


class WechatFundflowPostResponse(BaseModel):
    ok: bool
    posted: bool
    bill_date: str
    settlement_cny: float
    fee_cny: float
    message: str


class InfraCostPostRequest(BaseModel):
    billing_cycle: str
    vendor: str = "aliyun"


class InfraCostPostResponse(BaseModel):
    ok: bool
    posted: bool
    billing_cycle: str
    infra_cost_cny: float
    message: str


class AdminTransactionItem(BaseModel):
    id: UUID
    user_id: UUID
    phone: str | None = None
    display_name: str | None = None
    type: str
    credits_delta: int
    balance_after: int
    model_key: str | None = None
    raw_cost_cny: float | None = None
    revenue_cny: float | None = None
    memo: str | None = None
    created_at: datetime


class AdminLedgerLine(BaseModel):
    account_code: str
    account_name: str | None = None
    debit: float
    credit: float


class AdminLedgerEntryItem(BaseModel):
    id: UUID
    event_type: str
    event_ref: str | None = None
    memo: str | None = None
    posted_at: datetime
    lines: list[AdminLedgerLine]


class AdminUserItem(BaseModel):
    user_id: UUID
    phone: str | None = None
    display_name: str
    is_anonymous: bool
    paid_balance: int
    gift_balance: int
    total_recharged_credits: int
    total_spent_credits: int
