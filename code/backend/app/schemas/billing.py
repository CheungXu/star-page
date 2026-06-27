from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AccountResponse(BaseModel):
    is_anonymous: bool
    paid_balance: int
    gift_balance: int
    total_balance: int
    free_generations_used: int
    free_generations_limit: int
    free_generations_remaining: int
    signup_bonus_granted: bool


class TransactionItem(BaseModel):
    id: UUID
    type: str
    credits_delta: int
    balance_after: int
    model_key: str | None = None
    revenue_cny: float | None = None
    raw_cost_cny: float | None = None
    memo: str | None = None
    created_at: datetime


class PackageItem(BaseModel):
    key: str
    title: str
    amount_cny: float
    base_credits: int
    bonus_credits: int
    total_credits: int


class RechargeCreateRequest(BaseModel):
    package_key: str = Field(min_length=1, max_length=64)
    # 支付方式：wechat（微信 Native 扫码）/ mock（仅非生产）。不传时后端按环境选默认。
    provider: str | None = Field(default=None, max_length=16)


class RechargeOrderResponse(BaseModel):
    order_id: UUID
    package_key: str
    amount_cny: float
    base_credits: int
    bonus_credits: int
    status: str
    payment_provider: str
    pay_url: str | None = None
    # 微信 Native 下单返回的二维码链接，前端转二维码展示。
    code_url: str | None = None


class RechargeStatusResponse(BaseModel):
    order_id: UUID
    status: str
    payment_provider: str
    amount_cny: float
    total_credits: int


class MockPayResponse(BaseModel):
    order_id: UUID
    status: str
