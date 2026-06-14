from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_billing_config
from app.models.entities import (
    AnonVisitor,
    CreditAccount,
    CreditPackage,
    CreditTransaction,
    RechargeOrder,
    User,
)
from app.services.billing.errors import AnonLimitError, BillingError, InsufficientCreditsError, ModelNotAllowedError
from app.services.billing.ledger import LedgerLine, LedgerService
from app.services.billing.pricing import credits_for_cost, credits_to_cny


@dataclass(frozen=True)
class SettleResult:
    credits_charged: int
    paid_used: int
    gift_used: int
    balance_after: int
    already_settled: bool = False


@dataclass(frozen=True)
class AccountSummary:
    is_anonymous: bool
    paid_balance: int
    gift_balance: int
    total_balance: int
    free_generations_used: int
    free_generations_limit: int
    free_generations_remaining: int
    signup_bonus_granted: bool


class BillingService:
    """积分钱包与记账编排：赠送、充值入账、生成结算，全部幂等。

    钱包区分 paid（充值）与 gift（赠送）两个桶，消费时先扣 gift、后扣 paid。
    每个会引起余额变化的动作都会：更新钱包 + 写积分流水 + 复式过账。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.config = get_billing_config()
        self.ledger = LedgerService(session)

    # ---------- 钱包基础 ----------

    async def get_or_create_account(self, user_id: uuid.UUID) -> CreditAccount:
        account = await self.session.get(CreditAccount, user_id)
        if account is None:
            account = CreditAccount(user_id=user_id)
            self.session.add(account)
            await self.session.flush()
        return account

    async def _has_transaction(self, idempotency_key: str) -> bool:
        result = await self.session.execute(
            select(CreditTransaction.id).where(CreditTransaction.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none() is not None

    # ---------- 赠送（新注册 1000 积分） ----------

    async def grant_signup_bonus(self, user: User) -> int:
        """首次注册赠送积分，幂等（signup_bonus_granted 标记 + 流水唯一键）。返回实际赠送积分。"""
        credits = self.config.signup_bonus_credits
        if credits <= 0:
            return 0

        account = await self.get_or_create_account(user.id)
        if account.signup_bonus_granted:
            return 0

        idem = f"gift:signup:{user.id}"
        if await self._has_transaction(idem):
            account.signup_bonus_granted = True
            return 0

        account.gift_balance += credits
        account.signup_bonus_granted = True
        face = credits_to_cny(credits)

        self.session.add(
            CreditTransaction(
                id=uuid.uuid4(),
                user_id=user.id,
                type="gift",
                credits_delta=credits,
                gift_delta=credits,
                paid_delta=0,
                balance_after=account.paid_balance + account.gift_balance,
                ref_type="signup_bonus",
                ref_id=str(user.id),
                idempotency_key=idem,
                memo="新用户注册赠送",
            )
        )
        # 借 推广费 / 贷 赠送负债
        await self.ledger.post(
            event_type="gift_grant",
            event_ref=idem,
            user_id=user.id,
            memo="新用户注册赠送",
            lines=[
                LedgerLine(account_code="6601", debit=face),
                LedgerLine(account_code="2002", credit=face),
            ],
        )
        await self.session.flush()
        return credits

    # ---------- 充值入账 ----------

    async def apply_recharge(self, order: RechargeOrder) -> bool:
        """订单支付成功后入账，幂等。base→paid 桶，bonus→gift 桶。返回是否本次实际入账。"""
        idem = f"recharge:{order.id}"
        if await self._has_transaction(idem):
            return False

        account = await self.get_or_create_account(order.user_id)
        base = int(order.base_credits)
        bonus = int(order.bonus_credits or 0)
        account.paid_balance += base
        account.gift_balance += bonus
        account.total_recharged_credits += base + bonus

        self.session.add(
            CreditTransaction(
                id=uuid.uuid4(),
                user_id=order.user_id,
                type="recharge",
                credits_delta=base + bonus,
                paid_delta=base,
                gift_delta=bonus,
                balance_after=account.paid_balance + account.gift_balance,
                ref_type="recharge_order",
                ref_id=str(order.id),
                idempotency_key=idem,
                memo=f"充值套餐 {order.package_key}",
            )
        )

        amount = Decimal(str(order.amount_cny))
        lines = [
            LedgerLine(account_code="1001", debit=amount),
            LedgerLine(account_code="2001", credit=amount),
        ]
        if bonus > 0:
            bonus_face = credits_to_cny(bonus)
            lines.append(LedgerLine(account_code="6601", debit=bonus_face))
            lines.append(LedgerLine(account_code="2002", credit=bonus_face))

        await self.ledger.post(
            event_type="recharge_paid",
            event_ref=idem,
            user_id=order.user_id,
            memo=f"充值入账 {order.package_key}",
            lines=lines,
        )
        await self.session.flush()
        return True

    # ---------- 供应商预付（云账户充值） ----------

    async def record_supplier_topup(self, amount_cny: Decimal, memo: str | None = None) -> None:
        """记录向云/LLM 供应商账户预充值：借 预付账款(1102) / 贷 现金(1001)。
        每次为管理员显式操作，使用独立事件号，不做跨次幂等。"""
        amount = Decimal(str(amount_cny))
        if amount <= 0:
            raise BillingError("充值金额必须大于 0")
        await self.ledger.post(
            event_type="supplier_topup",
            event_ref=str(uuid.uuid4()),
            memo=memo or "云账户预充值",
            lines=[
                LedgerLine(account_code="1102", debit=amount),
                LedgerLine(account_code="1001", credit=amount),
            ],
        )
        await self.session.flush()

    @staticmethod
    def _infra_event_ref(vendor: str, billing_cycle: str) -> str:
        return f"{vendor}-infra:{billing_cycle}"

    async def is_infra_cost_posted(self, vendor: str, billing_cycle: str) -> bool:
        from app.models.entities import LedgerEntry

        result = await self.session.execute(
            select(LedgerEntry.id).where(
                LedgerEntry.event_type == "infra_cost",
                LedgerEntry.event_ref == self._infra_event_ref(vendor, billing_cycle),
            )
        )
        return result.scalar_one_or_none() is not None

    async def record_infra_cost(
        self, vendor: str, billing_cycle: str, amount_cny: Decimal, memo: str | None = None
    ) -> bool:
        """按账期把基础设施成本（服务器等，非 LLM）入账：借 6002 基础设施成本 / 贷 1102 预付账款。
        按 (infra_cost, vendor-账期) 幂等，重复入账同一账期不会重复记。返回是否本次实际入账。"""
        amount = Decimal(str(amount_cny))
        if amount <= 0:
            raise BillingError("基础设施成本金额必须大于 0")
        entry = await self.ledger.post(
            event_type="infra_cost",
            event_ref=self._infra_event_ref(vendor, billing_cycle),
            memo=memo or f"{vendor} {billing_cycle} 基础设施成本（非LLM）",
            lines=[
                LedgerLine(account_code="6002", debit=amount),
                LedgerLine(account_code="1102", credit=amount),
            ],
        )
        await self.session.flush()
        return entry is not None

    # ---------- 生成前置校验 ----------

    async def ensure_can_start_batch(self, user: User, model_keys: list[str]) -> None:
        """生成前置校验（额度/余额）。匿名：免费次数；登录：余额 > 0。模型白名单另由 validate_anon_models 校验。"""
        if user.is_anonymous:
            visitor = await self._get_visitor_by_user(user.id)
            used = visitor.free_generations_used if visitor else 0
            if used >= self.config.free_trial_generations:
                raise AnonLimitError("免费体验次数已用完，注册即可继续创建并获得 1000 积分")
            return

        account = await self.get_or_create_account(user.id)
        if account.paid_balance + account.gift_balance <= 0:
            raise InsufficientCreditsError("积分不足，请先充值后再创建")

    def validate_anon_models(self, model_keys: list[str]) -> None:
        if len(model_keys) > self.config.anon_max_models_per_gen:
            raise ModelNotAllowedError(
                f"未登录最多同时使用 {self.config.anon_max_models_per_gen} 个模型，注册后可解锁更多"
            )
        for key in model_keys:
            if not self.config.is_anon_allowed_model(key):
                raise ModelNotAllowedError("该模型需登录后使用，注册即可解锁全部模型")

    async def record_anon_batch_started(self, user: User) -> None:
        """匿名生成批次启动后计一次免费额度（按批次计，非按模型）。"""
        if not user.is_anonymous:
            return
        visitor = await self._get_visitor_by_user(user.id)
        if visitor is not None:
            visitor.free_generations_used += 1
        account = await self.get_or_create_account(user.id)
        account.free_generations_used += 1
        await self.session.flush()

    async def _get_visitor_by_user(self, user_id: uuid.UUID) -> AnonVisitor | None:
        result = await self.session.execute(select(AnonVisitor).where(AnonVisitor.user_id == user_id))
        return result.scalar_one_or_none()

    # ---------- 账户摘要 / 充值订单 ----------

    async def account_summary(self, user: User | None) -> AccountSummary:
        """供 /api/billing/account 使用。匿名（含无 cookie 的新访客）返回免费额度信息。"""
        limit = self.config.free_trial_generations
        if user is None:
            return AccountSummary(True, 0, 0, 0, 0, limit, limit, False)

        if user.is_anonymous:
            visitor = await self._get_visitor_by_user(user.id)
            used = visitor.free_generations_used if visitor else 0
            return AccountSummary(True, 0, 0, 0, used, limit, max(limit - used, 0), False)

        account = await self.get_or_create_account(user.id)
        await self.session.commit()
        total = account.paid_balance + account.gift_balance
        return AccountSummary(
            False,
            account.paid_balance,
            account.gift_balance,
            total,
            account.free_generations_used,
            limit,
            0,
            account.signup_bonus_granted,
        )

    async def list_active_packages(self) -> list[CreditPackage]:
        result = await self.session.execute(
            select(CreditPackage).where(CreditPackage.is_active.is_(True)).order_by(CreditPackage.sort_order.asc())
        )
        return list(result.scalars().all())

    async def create_recharge_order(
        self, user: User, package_key: str, *, provider: str = "mock"
    ) -> RechargeOrder:
        """建单：金额/积分一律服务端按套餐计算（价格服务端权威）。"""
        package = await self.session.get(CreditPackage, package_key)
        if package is None or not package.is_active:
            raise BillingError("套餐不存在或已下架")

        amount = Decimal(str(package.amount_cny))
        if amount < Decimal(str(self.config.min_recharge_cny)) or amount > Decimal(str(self.config.max_recharge_cny)):
            raise BillingError("充值金额超出允许范围")

        order = RechargeOrder(
            id=uuid.uuid4(),
            user_id=user.id,
            package_key=package.key,
            amount_cny=package.amount_cny,
            base_credits=package.base_credits,
            bonus_credits=package.bonus_credits,
            status="pending",
            payment_provider=provider,
        )
        self.session.add(order)
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def mark_order_paid(self, order_id: uuid.UUID, *, provider_txn_id: str | None = None) -> RechargeOrder:
        """原子流转 pending→paid（防重复回调），随后幂等入账。"""
        result = await self.session.execute(
            update(RechargeOrder)
            .where(RechargeOrder.id == order_id, RechargeOrder.status == "pending")
            .values(status="paid", paid_at=datetime.now(UTC), provider_txn_id=provider_txn_id)
            .returning(RechargeOrder.id)
        )
        transitioned = result.scalar_one_or_none()

        order = await self.session.get(RechargeOrder, order_id)
        if order is None:
            raise BillingError("订单不存在")

        if transitioned is not None:
            await self.apply_recharge(order)
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def list_transactions(self, user: User, *, limit: int = 50) -> list[CreditTransaction]:
        result = await self.session.execute(
            select(CreditTransaction)
            .where(CreditTransaction.user_id == user.id)
            .order_by(CreditTransaction.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # ---------- 生成结算 ----------

    async def settle_generation(
        self,
        *,
        user: User,
        version_id: uuid.UUID,
        model_key: str | None,
        raw_cost_cny: Decimal | float | None,
        markup: float,
    ) -> SettleResult:
        """按页面版本结算一次生成消耗，按 version_id 幂等。

        匿名：不扣积分（免费额度已在启动时计数），仅记 COGS 进试用获客成本。
        登录：先扣 gift 后扣 paid；确认收入 = 实扣积分面值；记 COGS。
        """
        idem = f"consume:{version_id}"
        raw_cost = Decimal(str(raw_cost_cny)) if raw_cost_cny is not None else Decimal("0")
        if raw_cost < 0:
            raw_cost = Decimal("0")

        if await self._has_transaction(idem):
            return SettleResult(0, 0, 0, 0, already_settled=True)

        if user.is_anonymous:
            return await self._settle_anonymous(user, version_id, idem, model_key, raw_cost, markup)
        return await self._settle_registered(user, version_id, idem, model_key, raw_cost, markup)

    async def _settle_anonymous(
        self,
        user: User,
        version_id: uuid.UUID,
        idem: str,
        model_key: str | None,
        raw_cost: Decimal,
        markup: float,
    ) -> SettleResult:
        self.session.add(
            CreditTransaction(
                id=uuid.uuid4(),
                user_id=user.id,
                type="consume",
                credits_delta=0,
                paid_delta=0,
                gift_delta=0,
                balance_after=0,
                ref_type="page_version",
                ref_id=str(version_id),
                model_key=model_key,
                raw_cost_cny=raw_cost,
                markup=Decimal(str(markup)),
                revenue_cny=Decimal("0"),
                idempotency_key=idem,
                memo="匿名试用消耗（无收入）",
            )
        )
        await self._post_cogs(version_id, idem, user.id, raw_cost, memo="匿名试用算力成本")
        await self.session.flush()
        return SettleResult(0, 0, 0, 0)

    async def _settle_registered(
        self,
        user: User,
        version_id: uuid.UUID,
        idem: str,
        model_key: str | None,
        raw_cost: Decimal,
        markup: float,
    ) -> SettleResult:
        account = await self.get_or_create_account(user.id)
        credits = credits_for_cost(raw_cost, markup)

        total_balance = account.paid_balance + account.gift_balance
        deduct = min(credits, total_balance)  # 受 CHECK>=0 约束，最多扣到 0，单次小额兜底
        gift_used = min(account.gift_balance, deduct)
        paid_used = deduct - gift_used

        account.gift_balance -= gift_used
        account.paid_balance -= paid_used
        account.total_spent_credits += deduct
        balance_after = account.paid_balance + account.gift_balance

        revenue = credits_to_cny(deduct)
        self.session.add(
            CreditTransaction(
                id=uuid.uuid4(),
                user_id=user.id,
                type="consume",
                credits_delta=-deduct,
                paid_delta=-paid_used,
                gift_delta=-gift_used,
                balance_after=balance_after,
                ref_type="page_version",
                ref_id=str(version_id),
                model_key=model_key,
                raw_cost_cny=raw_cost,
                markup=Decimal(str(markup)),
                revenue_cny=revenue,
                idempotency_key=idem,
                memo="生成消耗",
            )
        )

        # 确认收入：充值部分核销预收账款，赠送部分核销赠送负债；两者均确认服务收入。
        lines: list[LedgerLine] = []
        paid_face = credits_to_cny(paid_used)
        gift_face = credits_to_cny(gift_used)
        if paid_used > 0:
            lines.append(LedgerLine(account_code="2001", debit=paid_face))
            lines.append(LedgerLine(account_code="5001", credit=paid_face))
        if gift_used > 0:
            lines.append(LedgerLine(account_code="2002", debit=gift_face))
            lines.append(LedgerLine(account_code="5001", credit=gift_face))
        # 算力成本：冲减预付云资源（预付费模型）
        if raw_cost > 0:
            lines.append(LedgerLine(account_code="6001", debit=raw_cost))
            lines.append(LedgerLine(account_code="1102", credit=raw_cost))

        if lines:
            await self.ledger.post(
                event_type="consume",
                event_ref=idem,
                user_id=user.id,
                memo="生成消耗确认收入与成本",
                lines=lines,
            )
        await self.session.flush()
        return SettleResult(deduct, paid_used, gift_used, balance_after)

    async def _post_cogs(
        self, version_id: uuid.UUID, idem: str, user_id: uuid.UUID, raw_cost: Decimal, *, memo: str
    ) -> None:
        if raw_cost <= 0:
            return
        await self.ledger.post(
            event_type="consume",
            event_ref=idem,
            user_id=user_id,
            memo=memo,
            lines=[
                LedgerLine(account_code="6001", debit=raw_cost),
                LedgerLine(account_code="1102", credit=raw_cost),
            ],
        )
