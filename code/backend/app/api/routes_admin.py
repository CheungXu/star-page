from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.core.auth import require_admin
from app.core.config import get_billing_config, get_model_registry, update_billing_markups
from app.core.database import AsyncSessionLocal
from app.models.entities import (
    CreditAccount,
    CreditTransaction,
    LedgerAccount,
    LedgerEntry,
    LedgerEntryLine,
    User,
)
from app.schemas.admin import (
    AdminLedgerEntryItem,
    AdminLedgerLine,
    AdminOverview,
    AdminTransactionItem,
    AdminUserItem,
    AliyunBillOverviewResponse,
    AliyunBillProductItem,
    InfraCostPostRequest,
    InfraCostPostResponse,
    LedgerAccountStat,
    ModelMarkupConfig,
    ModelMarkupItem,
    ModelMarkupUpdateRequest,
    SupplierBalanceItem,
    SupplierTopupRequest,
    SupplierTopupResponse,
)
from app.services.billing.account import BillingService
from app.services.supplier_balance import get_supplier_balances
from app.services.supplier_balance.aliyun import fetch_aliyun_bill_overview

router = APIRouter(prefix="/api/admin/billing", tags=["admin"])


def _f(value) -> float:
    return float(value) if value is not None else 0.0


@router.get("/overview", response_model=AdminOverview)
async def overview(request: Request) -> AdminOverview:
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)

        # 各科目借贷汇总
        rows = await session.execute(
            select(
                LedgerEntryLine.account_code,
                func.coalesce(func.sum(LedgerEntryLine.debit), 0),
                func.coalesce(func.sum(LedgerEntryLine.credit), 0),
            ).group_by(LedgerEntryLine.account_code)
        )
        sums: dict[str, tuple[Decimal, Decimal]] = {
            code: (Decimal(str(debit)), Decimal(str(credit))) for code, debit, credit in rows.all()
        }

        accounts_meta = {a.code: a for a in (await session.execute(select(LedgerAccount))).scalars().all()}

        def debit_net(code: str) -> Decimal:
            d, c = sums.get(code, (Decimal("0"), Decimal("0")))
            return d - c

        def credit_net(code: str) -> Decimal:
            d, c = sums.get(code, (Decimal("0"), Decimal("0")))
            return c - d

        def debit_sum(code: str) -> Decimal:
            return sums.get(code, (Decimal("0"), Decimal("0")))[0]

        # 累计充值现金取借方累计（不被供应商付款冲减），现金净额另算。
        total_recharge = debit_sum("1001")
        gift_granted = debit_net("6601")
        gift_unused = credit_net("2002")
        deferred_revenue = credit_net("2001")
        # 预付云资源：借方=累计向云账户充值，贷方=调用消费冲减；净额=剩余可用。
        prepaid_cloud_topup = debit_sum("1102")
        prepaid_cloud_balance = debit_net("1102")

        # 按 paid/gift 拆分收入与成本：收入由消费流水的 paid_delta/gift_delta 决定，
        # 成本按该次消费中 paid/gift 占比对原始成本做归集；用尽=0(匿名/未回收)单列为试用获客成本。
        consume_rows = await session.execute(
            select(
                CreditTransaction.paid_delta,
                CreditTransaction.gift_delta,
                CreditTransaction.raw_cost_cny,
            ).where(CreditTransaction.type == "consume")
        )
        paid_revenue = Decimal("0")
        gift_revenue = Decimal("0")
        paid_cogs = Decimal("0")
        gift_cogs = Decimal("0")
        trial_cogs = Decimal("0")
        for paid_delta, gift_delta, raw_cost in consume_rows.all():
            paid_used = Decimal(str(-(paid_delta or 0)))
            gift_used = Decimal(str(-(gift_delta or 0)))
            cost = Decimal(str(raw_cost)) if raw_cost is not None else Decimal("0")
            paid_revenue += paid_used / Decimal("100")
            gift_revenue += gift_used / Decimal("100")
            used = paid_used + gift_used
            if used <= 0:
                trial_cogs += cost
            else:
                paid_cogs += cost * paid_used / used
                gift_cogs += cost * gift_used / used

        total_revenue = paid_revenue + gift_revenue
        total_cogs = paid_cogs + gift_cogs + trial_cogs
        paid_gross = paid_revenue - paid_cogs
        total_gross = total_revenue - total_cogs
        infra_cost = debit_net("6002")
        operating_profit = total_gross - infra_cost
        paid_margin = float(paid_gross / paid_revenue) if paid_revenue > 0 else None
        total_margin = float(total_gross / total_revenue) if total_revenue > 0 else None

        balances = await session.execute(
            select(
                func.coalesce(func.sum(CreditAccount.paid_balance), 0),
                func.coalesce(func.sum(CreditAccount.gift_balance), 0),
            )
        )
        paid_sum, gift_sum = balances.one()

        user_count = int(
            (await session.execute(select(func.count(User.id)).where(User.is_anonymous.is_(False)))).scalar_one()
        )

        account_stats = []
        for code, (d, c) in sorted(sums.items()):
            meta = accounts_meta.get(code)
            account_stats.append(
                LedgerAccountStat(
                    code=code,
                    name=meta.name if meta else code,
                    type=meta.type if meta else "",
                    debit=float(d),
                    credit=float(c),
                    net=float(d - c),
                )
            )

        return AdminOverview(
            total_recharge_cny=float(total_recharge),
            paid_revenue_cny=float(paid_revenue),
            paid_cogs_cny=float(paid_cogs),
            paid_gross_profit_cny=float(paid_gross),
            paid_gross_margin=paid_margin,
            gift_granted_cny=float(gift_granted),
            gift_unused_cny=float(gift_unused),
            gift_revenue_cny=float(gift_revenue),
            gift_cogs_cny=float(gift_cogs),
            trial_cogs_cny=float(trial_cogs),
            total_revenue_cny=float(total_revenue),
            total_cogs_cny=float(total_cogs),
            total_gross_profit_cny=float(total_gross),
            total_gross_margin=total_margin,
            infra_cost_cny=float(infra_cost),
            operating_profit_cny=float(operating_profit),
            deferred_revenue_cny=float(deferred_revenue),
            prepaid_cloud_balance_cny=float(prepaid_cloud_balance),
            prepaid_cloud_topup_cny=float(prepaid_cloud_topup),
            total_paid_balance_credits=int(paid_sum),
            total_gift_balance_credits=int(gift_sum),
            user_count=user_count,
            accounts=account_stats,
        )


@router.get("/transactions", response_model=list[AdminTransactionItem])
async def transactions(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> list[AdminTransactionItem]:
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        rows = await session.execute(
            select(CreditTransaction, User)
            .join(User, User.id == CreditTransaction.user_id)
            .order_by(CreditTransaction.created_at.desc())
            .limit(limit)
        )
        items: list[AdminTransactionItem] = []
        for txn, user in rows.all():
            items.append(
                AdminTransactionItem(
                    id=txn.id,
                    user_id=txn.user_id,
                    phone=user.phone,
                    display_name=user.display_name,
                    type=txn.type,
                    credits_delta=txn.credits_delta,
                    balance_after=txn.balance_after,
                    model_key=txn.model_key,
                    raw_cost_cny=_f(txn.raw_cost_cny) if txn.raw_cost_cny is not None else None,
                    revenue_cny=_f(txn.revenue_cny) if txn.revenue_cny is not None else None,
                    memo=txn.memo,
                    created_at=txn.created_at,
                )
            )
        return items


@router.get("/ledger", response_model=list[AdminLedgerEntryItem])
async def ledger(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> list[AdminLedgerEntryItem]:
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        entries = (
            await session.execute(select(LedgerEntry).order_by(LedgerEntry.posted_at.desc()).limit(limit))
        ).scalars().all()
        entry_ids = [e.id for e in entries]

        lines_by_entry: dict = defaultdict(list)
        if entry_ids:
            account_names = {
                a.code: a.name for a in (await session.execute(select(LedgerAccount))).scalars().all()
            }
            line_rows = (
                await session.execute(
                    select(LedgerEntryLine).where(LedgerEntryLine.entry_id.in_(entry_ids))
                )
            ).scalars().all()
            for line in line_rows:
                lines_by_entry[line.entry_id].append(
                    AdminLedgerLine(
                        account_code=line.account_code,
                        account_name=account_names.get(line.account_code),
                        debit=_f(line.debit),
                        credit=_f(line.credit),
                    )
                )

        return [
            AdminLedgerEntryItem(
                id=e.id,
                event_type=e.event_type,
                event_ref=e.event_ref,
                memo=e.memo,
                posted_at=e.posted_at,
                lines=lines_by_entry.get(e.id, []),
            )
            for e in entries
        ]


@router.get("/users", response_model=list[AdminUserItem])
async def users(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> list[AdminUserItem]:
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        rows = await session.execute(
            select(User, CreditAccount)
            .join(CreditAccount, CreditAccount.user_id == User.id)
            .order_by(CreditAccount.total_recharged_credits.desc(), CreditAccount.updated_at.desc())
            .limit(limit)
        )
        items: list[AdminUserItem] = []
        for user, account in rows.all():
            items.append(
                AdminUserItem(
                    user_id=user.id,
                    phone=user.phone,
                    display_name=user.display_name,
                    is_anonymous=user.is_anonymous,
                    paid_balance=account.paid_balance,
                    gift_balance=account.gift_balance,
                    total_recharged_credits=account.total_recharged_credits,
                    total_spent_credits=account.total_spent_credits,
                )
            )
        return items


def _pricing_summary(pricing: dict | None) -> str | None:
    if not pricing:
        return None
    tiers = pricing.get("tiers") or []
    if not tiers:
        return None
    tier = tiers[0]
    currency = pricing.get("currency") or "CNY"
    symbol = "¥" if currency == "CNY" else f"{currency} "
    inp = tier.get("input_per_million")
    out = tier.get("output_per_million")
    parts: list[str] = []
    if inp is not None:
        parts.append(f"输入 {symbol}{inp}/百万 tokens")
    if out is not None:
        parts.append(f"输出 {symbol}{out}/百万 tokens")
    return " · ".join(parts) if parts else None


def _build_markup_config() -> ModelMarkupConfig:
    billing = get_billing_config()
    registry = get_model_registry()
    items: list[ModelMarkupItem] = []
    for key, model in registry.models.items():
        custom = key in billing.model_markups
        items.append(
            ModelMarkupItem(
                key=key,
                label=model.label,
                provider=model.provider,
                available=model.available,
                markup=float(billing.model_markups.get(key, billing.default_markup)),
                is_custom=custom,
                pricing_summary=_pricing_summary(model.pricing),
            )
        )
    items.sort(key=lambda x: (x.provider, x.label))
    return ModelMarkupConfig(default_markup=billing.default_markup, models=items)


@router.get("/model-markups", response_model=ModelMarkupConfig)
async def get_model_markups(request: Request) -> ModelMarkupConfig:
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
    return _build_markup_config()


@router.put("/model-markups", response_model=ModelMarkupConfig)
async def put_model_markups(request: Request, payload: ModelMarkupUpdateRequest) -> ModelMarkupConfig:
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)

    if payload.default_markup <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="默认倍率必须大于 0")

    registry = get_model_registry()
    known_keys = set(registry.models.keys())
    cleaned: dict[str, float] = {}
    for key, value in payload.model_markups.items():
        if key not in known_keys:
            # 跳过未知模型键，避免脏数据写回配置
            continue
        if value <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"模型 {key} 的倍率必须大于 0")
        cleaned[key] = float(value)

    try:
        update_billing_markups(payload.default_markup, cleaned)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return _build_markup_config()


@router.post("/supplier-topup", response_model=SupplierTopupResponse)
async def supplier_topup(request: Request, payload: SupplierTopupRequest) -> SupplierTopupResponse:
    if payload.amount_cny <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="充值金额必须大于 0")
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        service = BillingService(session)
        await service.record_supplier_topup(Decimal(str(payload.amount_cny)), payload.memo)
        await session.commit()

        rows = await session.execute(
            select(
                func.coalesce(func.sum(LedgerEntryLine.debit), 0),
                func.coalesce(func.sum(LedgerEntryLine.credit), 0),
            ).where(LedgerEntryLine.account_code == "1102")
        )
        debit, credit = rows.one()
        topup = Decimal(str(debit))
        balance = Decimal(str(debit)) - Decimal(str(credit))
        return SupplierTopupResponse(
            ok=True,
            prepaid_cloud_balance_cny=float(balance),
            prepaid_cloud_topup_cny=float(topup),
        )


@router.get("/supplier-balances", response_model=list[SupplierBalanceItem])
async def supplier_balances(request: Request, refresh: bool = Query(default=False)) -> list[SupplierBalanceItem]:
    """各云/LLM 供应商账户真实余额（对账用）。默认走 5 分钟缓存，refresh=true 强制刷新。"""
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
    balances = await get_supplier_balances(force=refresh)
    return [
        SupplierBalanceItem(
            vendor=b.vendor,
            label=b.label,
            configured=b.configured,
            available_amount=b.available_amount,
            available_cash_amount=b.available_cash_amount,
            currency=b.currency,
            fetched_at=b.fetched_at,
            error=b.error,
            note=b.note,
        )
        for b in balances
    ]


def _default_billing_cycle() -> str:
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"{now.year:04d}-{now.month:02d}"


async def _posted_infra_amount(session, vendor: str, cycle: str) -> Decimal:
    event_ref = BillingService._infra_event_ref(vendor, cycle)
    row = await session.execute(
        select(func.coalesce(func.sum(LedgerEntryLine.debit), 0))
        .join(LedgerEntry, LedgerEntry.id == LedgerEntryLine.entry_id)
        .where(
            LedgerEntry.event_type == "infra_cost",
            LedgerEntry.event_ref == event_ref,
            LedgerEntryLine.account_code == "6002",
        )
    )
    return Decimal(str(row.scalar_one()))


def _cycle_bounds(billing_cycle: str) -> tuple[datetime, datetime]:
    """把账期 YYYY-MM 转成 [本月初, 下月初) 的时间窗（按 UTC+8 阿里云出账时区）。"""
    year, month = (int(x) for x in billing_cycle.split("-")[:2])
    tz = timezone(timedelta(hours=8))
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + 1, 1, 1, tzinfo=tz) if month == 12 else datetime(year, month + 1, 1, tzinfo=tz)
    return start, end


def _aliyun_model_keys() -> list[str]:
    """识别走阿里云百炼/DashScope 计费的模型（按 base_url 域名）。"""
    registry = get_model_registry()
    keys: list[str] = []
    for key, model in registry.models.items():
        url = (getattr(model, "base_url", "") or "").lower()
        if "dashscope" in url or "aliyuncs.com" in url:
            keys.append(key)
    return keys


async def _estimated_aliyun_llm_cogs(session, billing_cycle: str) -> Decimal:
    """该账期内，阿里云模型按次估算的原始成本之和（我们记账的 6001 来源）。"""
    keys = _aliyun_model_keys()
    if not keys:
        return Decimal("0")
    start, end = _cycle_bounds(billing_cycle)
    row = await session.execute(
        select(func.coalesce(func.sum(CreditTransaction.raw_cost_cny), 0)).where(
            CreditTransaction.type == "consume",
            CreditTransaction.model_key.in_(keys),
            CreditTransaction.created_at >= start,
            CreditTransaction.created_at < end,
        )
    )
    return Decimal(str(row.scalar_one()))


@router.get("/aliyun-bill", response_model=AliyunBillOverviewResponse)
async def aliyun_bill(request: Request, cycle: str = Query(default="")) -> AliyunBillOverviewResponse:
    """拉取阿里云某账期账单总览，按产品拆分百炼LLM与基础设施，并与按次估算COGS做偏差对账。cycle 形如 2026-06，默认当月。"""
    billing_cycle = cycle.strip() or _default_billing_cycle()
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        service = BillingService(session)
        posted = await service.is_infra_cost_posted("aliyun", billing_cycle)
        posted_amount = await _posted_infra_amount(session, "aliyun", billing_cycle) if posted else Decimal("0")
        estimated = await _estimated_aliyun_llm_cogs(session, billing_cycle)

    keywords = get_billing_config().aliyun_llm_keywords
    overview = await asyncio.to_thread(fetch_aliyun_bill_overview, billing_cycle, keywords)

    actual_llm = Decimal(str(overview.llm_total))
    deviation = actual_llm - estimated
    deviation_pct = float(deviation / estimated) if estimated > 0 else None

    return AliyunBillOverviewResponse(
        configured=overview.configured,
        billing_cycle=overview.billing_cycle,
        items=[
            AliyunBillProductItem(
                product_name=i.product_name, product_code=i.product_code, amount=i.amount, is_llm=i.is_llm
            )
            for i in overview.items
        ],
        llm_total=overview.llm_total,
        infra_total=overview.infra_total,
        total=overview.total,
        currency=overview.currency,
        gross_total=overview.gross_total,
        coupon_deducted=overview.coupon_deducted,
        prepaid_card_deducted=overview.prepaid_card_deducted,
        payment_total=overview.payment_total,
        posted=posted,
        posted_infra_cny=float(posted_amount),
        estimated_llm_cogs_cny=float(estimated),
        llm_actual_cny=float(actual_llm),
        llm_deviation_cny=float(deviation),
        llm_deviation_pct=deviation_pct,
        error=overview.error,
        note=overview.note,
    )


@router.post("/aliyun-bill/post", response_model=InfraCostPostResponse)
async def aliyun_bill_post(request: Request, payload: InfraCostPostRequest) -> InfraCostPostResponse:
    """把某账期基础设施成本（剔除百炼LLM后）入账：借 6002 / 贷 1102。金额由服务端按账单重算，幂等。"""
    cycle = payload.billing_cycle.strip()
    if not cycle:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少账期 billing_cycle")
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)

        keywords = get_billing_config().aliyun_llm_keywords
        overview = await asyncio.to_thread(fetch_aliyun_bill_overview, cycle, keywords)
        if not overview.configured:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=overview.note or "未配置费用 AccessKey")
        if overview.error:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=overview.error)

        infra = Decimal(str(overview.infra_total))
        service = BillingService(session)
        if infra <= 0:
            return InfraCostPostResponse(
                ok=True,
                posted=False,
                billing_cycle=cycle,
                infra_cost_cny=0.0,
                message=f"{cycle} 账单基础设施成本为 0，无需入账",
            )
        newly = await service.record_infra_cost("aliyun", cycle, infra, memo=f"阿里云 {cycle} 基础设施成本（剔除百炼）")
        await session.commit()
        return InfraCostPostResponse(
            ok=True,
            posted=newly,
            billing_cycle=cycle,
            infra_cost_cny=float(infra),
            message=(f"已入账基础设施成本 ¥{infra:.2f}" if newly else f"{cycle} 已入账过，未重复记账"),
        )
