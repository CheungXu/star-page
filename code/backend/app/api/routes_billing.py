from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status

from app.core.auth import get_current_user, get_optional_actor
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.entities import RechargeOrder
from app.schemas.billing import (
    AccountResponse,
    MockPayResponse,
    PackageItem,
    RechargeCreateRequest,
    RechargeOrderResponse,
    TransactionItem,
)
from app.services.billing import BillingService
from app.services.billing.errors import BillingError

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/account", response_model=AccountResponse)
async def get_account(request: Request) -> AccountResponse:
    async with AsyncSessionLocal() as session:
        user = await get_optional_actor(session, request)
        summary = await BillingService(session).account_summary(user)
        return AccountResponse(**summary.__dict__)


@router.get("/transactions", response_model=list[TransactionItem])
async def list_transactions(request: Request) -> list[TransactionItem]:
    async with AsyncSessionLocal() as session:
        user = await get_optional_actor(session, request)
        if user is None:
            return []
        rows = await BillingService(session).list_transactions(user)
        return [
            TransactionItem(
                id=row.id,
                type=row.type,
                credits_delta=row.credits_delta,
                balance_after=row.balance_after,
                model_key=row.model_key,
                revenue_cny=float(row.revenue_cny) if row.revenue_cny is not None else None,
                raw_cost_cny=float(row.raw_cost_cny) if row.raw_cost_cny is not None else None,
                memo=row.memo,
                created_at=row.created_at,
            )
            for row in rows
        ]


@router.get("/packages", response_model=list[PackageItem])
async def list_packages() -> list[PackageItem]:
    async with AsyncSessionLocal() as session:
        packages = await BillingService(session).list_active_packages()
        return [
            PackageItem(
                key=p.key,
                title=p.title,
                amount_cny=float(p.amount_cny),
                base_credits=int(p.base_credits),
                bonus_credits=int(p.bonus_credits),
                total_credits=int(p.base_credits) + int(p.bonus_credits),
            )
            for p in packages
        ]


@router.post("/recharge", response_model=RechargeOrderResponse)
async def create_recharge(payload: RechargeCreateRequest, request: Request) -> RechargeOrderResponse:
    async with AsyncSessionLocal() as session:
        user = await get_current_user(session, request)
        try:
            order = await BillingService(session).create_recharge_order(user, payload.package_key)
        except BillingError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

        # 真实支付接入后，这里返回支付网关下单地址；当前预留 mock 支付入口。
        pay_url = _mock_pay_url(order.id) if _mock_pay_enabled() else None
        return RechargeOrderResponse(
            order_id=order.id,
            package_key=order.package_key,
            amount_cny=float(order.amount_cny),
            base_credits=int(order.base_credits),
            bonus_credits=int(order.bonus_credits),
            status=order.status,
            payment_provider=order.payment_provider,
            pay_url=pay_url,
        )


@router.post("/recharge/{order_id}/mock-pay", response_model=MockPayResponse)
async def mock_pay(order_id: uuid.UUID, request: Request) -> MockPayResponse:
    """mock 支付回调：仅非生产环境开放，杜绝自助发积分。"""
    if not _mock_pay_enabled():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mock 支付在生产环境已禁用")

    async with AsyncSessionLocal() as session:
        user = await get_current_user(session, request)
        order = await session.get(RechargeOrder, order_id)
        if order is None or order.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在")

        try:
            order = await BillingService(session).mark_order_paid(
                order_id, provider_txn_id=f"mock-{order_id}"
            )
        except BillingError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": exc.code, "message": exc.message},
            ) from exc
        return MockPayResponse(order_id=order.id, status=order.status)


def _mock_pay_enabled() -> bool:
    return (get_settings().app_env or "").lower() != "production"


def _mock_pay_url(order_id: uuid.UUID) -> str:
    return f"/api/billing/recharge/{order_id}/mock-pay"
