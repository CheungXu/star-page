from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.core.auth import get_current_user, get_optional_actor
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.entities import CreditPackage, RechargeOrder
from app.schemas.billing import (
    AccountResponse,
    MockPayResponse,
    PackageItem,
    RechargeCreateRequest,
    RechargeOrderResponse,
    RechargeStatusResponse,
    TransactionItem,
)
from app.services.billing import BillingService
from app.services.billing.errors import BillingError
from app.services.payment import wechat

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
        service = BillingService(session)

        provider = (payload.provider or "").strip().lower() or _default_provider()
        if provider not in ("wechat", "mock"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不支持的支付方式"
            )
        if provider == "mock" and not _mock_pay_enabled():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mock 支付在生产环境已禁用")
        if provider == "wechat" and not wechat.is_configured():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="微信支付未配置，暂不可用"
            )

        try:
            order = await service.create_recharge_order(user, payload.package_key, provider=provider)
        except BillingError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

        pay_url: str | None = None
        code_url: str | None = None
        if provider == "mock":
            pay_url = _mock_pay_url(order.id)
        else:
            package = await session.get(CreditPackage, order.package_key)
            title = package.title if package else order.package_key
            description = f"星页-积分充值-{title}"
            amount_fen = int(round(float(order.amount_cny) * 100))
            try:
                code_url = await asyncio.to_thread(
                    wechat.native_prepay,
                    out_trade_no=order.out_trade_no,
                    description=description,
                    amount_fen=amount_fen,
                )
            except wechat.WechatPayError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail=f"微信下单失败：{exc.message}"
                ) from exc

        return RechargeOrderResponse(
            order_id=order.id,
            package_key=order.package_key,
            amount_cny=float(order.amount_cny),
            base_credits=int(order.base_credits),
            bonus_credits=int(order.bonus_credits),
            status=order.status,
            payment_provider=order.payment_provider,
            pay_url=pay_url,
            code_url=code_url,
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


@router.get("/recharge/{order_id}", response_model=RechargeStatusResponse)
async def recharge_status(order_id: uuid.UUID, request: Request) -> RechargeStatusResponse:
    """查询充值订单状态。微信单仍 pending 时主动查微信对账（回调丢失也能到账）。"""
    async with AsyncSessionLocal() as session:
        user = await get_current_user(session, request)
        order = await session.get(RechargeOrder, order_id)
        if order is None or order.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在")

        if (
            order.status == "pending"
            and order.payment_provider == "wechat"
            and order.out_trade_no
            and wechat.is_configured()
        ):
            try:
                info = await asyncio.to_thread(wechat.query_order, order.out_trade_no)
            except wechat.WechatPayError:
                info = None
            if info and info.get("trade_state") == "SUCCESS":
                amount_total = (info.get("amount") or {}).get("total")
                if amount_total == int(round(float(order.amount_cny) * 100)):
                    try:
                        order = await BillingService(session).mark_order_paid(
                            order.id, provider_txn_id=info.get("transaction_id")
                        )
                    except BillingError:
                        pass

        return RechargeStatusResponse(
            order_id=order.id,
            status=order.status,
            payment_provider=order.payment_provider,
            amount_cny=float(order.amount_cny),
            total_credits=int(order.base_credits) + int(order.bonus_credits),
        )


@router.post("/wechat/notify")
async def wechat_notify(request: Request) -> JSONResponse:
    """微信支付结果回调：验签 + 解密 + 校验金额/归属/状态后入账。无需登录。"""
    body = await request.body()
    headers = {
        "Wechatpay-Signature": request.headers.get("Wechatpay-Signature", ""),
        "Wechatpay-Timestamp": request.headers.get("Wechatpay-Timestamp", ""),
        "Wechatpay-Nonce": request.headers.get("Wechatpay-Nonce", ""),
        "Wechatpay-Serial": request.headers.get("Wechatpay-Serial", ""),
        "Wechatpay-Signature-Type": request.headers.get("Wechatpay-Signature-Type", ""),
    }

    try:
        result = await asyncio.to_thread(wechat.verify_and_parse_callback, headers, body)
    except Exception:  # noqa: BLE001 - 验签/解密异常统一视为失败
        result = None

    if not result:
        return JSONResponse(status_code=401, content={"code": "FAIL", "message": "验签失败"})

    # 非「支付成功」事件：回 200 表示已接收，避免微信反复重试。
    if result.get("event_type") != "TRANSACTION.SUCCESS":
        return JSONResponse(status_code=200, content={"code": "SUCCESS", "message": "OK"})

    resource = result.get("resource") or {}
    out_trade_no = resource.get("out_trade_no")
    trade_state = resource.get("trade_state")
    transaction_id = resource.get("transaction_id")
    amount_total = (resource.get("amount") or {}).get("total")

    if trade_state != "SUCCESS" or not out_trade_no:
        return JSONResponse(status_code=200, content={"code": "SUCCESS", "message": "OK"})

    order_id = _order_id_from_out_trade_no(out_trade_no)
    if order_id is None:
        return JSONResponse(status_code=200, content={"code": "SUCCESS", "message": "OK"})

    async with AsyncSessionLocal() as session:
        order = await session.get(RechargeOrder, order_id)
        if order is None or order.out_trade_no != out_trade_no:
            # 找不到订单也回 200，避免无意义重试（异常订单走后台对账）。
            return JSONResponse(status_code=200, content={"code": "SUCCESS", "message": "OK"})

        # 金额比对：服务端权威，回调金额必须与订单一致。
        if amount_total != int(round(float(order.amount_cny) * 100)):
            return JSONResponse(status_code=400, content={"code": "FAIL", "message": "金额不一致"})

        try:
            await BillingService(session).mark_order_paid(order.id, provider_txn_id=transaction_id)
        except BillingError:
            return JSONResponse(status_code=500, content={"code": "FAIL", "message": "入账失败"})

    return JSONResponse(status_code=200, content={"code": "SUCCESS", "message": "成功"})


def _order_id_from_out_trade_no(out_trade_no: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(hex=out_trade_no)
    except (ValueError, TypeError):
        return None


def _mock_pay_enabled() -> bool:
    return (get_settings().app_env or "").lower() != "production"


def _default_provider() -> str:
    # 微信已配置则默认走微信，否则回退 mock（开发期）。
    return "wechat" if wechat.is_configured() else "mock"


def _mock_pay_url(order_id: uuid.UUID) -> str:
    return f"/api/billing/recharge/{order_id}/mock-pay"
