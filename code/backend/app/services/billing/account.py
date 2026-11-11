from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_billing_config, get_model_registry
from app.models.entities import (
    AnonVisitor,
    CreditAccount,
    CreditPackage,
    CreditTransaction,
    GenerationBatch,
    GenerationTask,
    RechargeOrder,
    User,
)
from app.services.billing.errors import (
    AnonLimitError,
    BillingError,
    FlagshipUpgradeRequiredError,
    InsufficientCreditsError,
    ModelNotAllowedError,
)
from app.services.billing.ledger import LedgerLine, LedgerService
from app.services.billing.pricing import (
    apply_training_discount,
    credits_for_cost,
    credits_to_cny,
    discounted_hold_credits,
)


@dataclass(frozen=True)
class SettleResult:
    credits_charged: int
    paid_used: int
    gift_used: int
    balance_after: int
    already_settled: bool = False
    discount_credits: int = 0
    reserved_credits: int = 0
    refunded_credits: int = 0
    debt_added: int = 0


@dataclass(frozen=True)
class TaskHoldAllocation:
    model_key: str
    reserved_gift: int
    reserved_paid: int

    @property
    def total(self) -> int:
        return self.reserved_gift + self.reserved_paid


@dataclass(frozen=True)
class BatchReservation:
    used_flagship_trial: bool = False
    allocations: list[TaskHoldAllocation] = field(default_factory=list)

    @property
    def total_reserved(self) -> int:
        return sum(item.total for item in self.allocations)


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
    frozen_balance: int = 0
    debt_credits: int = 0
    is_paid_user: bool = False
    free_flagship_batches_remaining: int = 0


class BillingService:
    """积分钱包与记账编排：赠送、充值入账、动态预授权冻结、生成结算，全部幂等。

    钱包区分 paid（充值）与 gift（赠送）两个可用桶，另设 frozen_paid / frozen_gift 预冻结桶与 debt_credits 欠费桶。
    消费与预授权时均先扣 gift、后扣 paid。预授权与解冻仅在钱包桶间腾挪，实际结算或欠费冲抵才确认收入并过复式凭证。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.config = get_billing_config()
        self.ledger = LedgerService(session)

    # ---------- 钱包基础 ----------

    async def get_or_create_account(self, user_id: uuid.UUID, *, for_update: bool = False) -> CreditAccount:
        if for_update:
            account = await self.session.get(
                CreditAccount, user_id, populate_existing=True, with_for_update=True
            )
            if account is not None:
                return account
        else:
            account = await self.session.get(CreditAccount, user_id)
            if account is not None:
                return account

        account = CreditAccount(user_id=user_id)
        self.session.add(account)
        await self.session.flush()
        if for_update:
            account = await self.session.get(
                CreditAccount, user_id, populate_existing=True, with_for_update=True
            )
            assert account is not None
        return account

    async def _has_transaction(self, idempotency_key: str) -> bool:
        result = await self.session.execute(
            select(CreditTransaction.id).where(CreditTransaction.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    def _effective_balance(account: CreditAccount) -> int:
        """用户归属总净积分（可用 + 冻结），供流水 balance_after 快照与后台核对。"""
        return (
            int(account.paid_balance)
            + int(account.gift_balance)
            + int(account.frozen_paid_balance)
            + int(account.frozen_gift_balance)
        )

    # ---------- 赠送（新注册 450 积分 + 1 次旗舰双模首秀券） ----------

    async def grant_signup_bonus(self, user: User) -> int:
        """首次注册赠送积分与旗舰首秀券，幂等（signup_bonus_granted 标记 + 流水唯一键）。返回实际赠送积分。"""
        credits = self.config.signup_bonus_credits
        account = await self.get_or_create_account(user.id, for_update=True)
        if not account.flagship_trial_granted and account.total_recharged_credits == 0:
            account.free_flagship_batches_remaining = self.config.signup_flagship_trial_batches
            account.flagship_trial_granted = True

        if credits <= 0 or account.signup_bonus_granted:
            await self.session.flush()
            return 0

        idem = f"gift:signup:{user.id}"
        if await self._has_transaction(idem):
            account.signup_bonus_granted = True
            await self.session.flush()
            return 0

        account.gift_balance += credits
        account.signup_bonus_granted = True
        await self._settle_debt_from_available(
            account, user_id=user.id, ref_id=str(user.id), idem_suffix=f"signup:{user.id}"
        )
        face = credits_to_cny(credits)

        self.session.add(
            CreditTransaction(
                id=uuid.uuid4(),
                user_id=user.id,
                type="gift",
                credits_delta=credits,
                gift_delta=credits,
                paid_delta=0,
                balance_after=self._effective_balance(account),
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

    # ---------- 欠费冲抵 ----------

    async def _settle_debt_from_available(
        self,
        account: CreditAccount,
        *,
        user_id: uuid.UUID,
        ref_id: str,
        idem_suffix: str,
    ) -> int:
        """用当前可用余额优先冲抵 debt_credits（先扣 gift 后扣 paid），写 debt_settle 流水并确认递延收入。"""
        if account.debt_credits <= 0:
            return 0
        idem = f"debt_settle:{idem_suffix}"
        if await self._has_transaction(idem):
            return 0

        available = int(account.gift_balance) + int(account.paid_balance)
        repay = min(int(account.debt_credits), available)
        if repay <= 0:
            return 0

        gift_used = min(int(account.gift_balance), repay)
        paid_used = repay - gift_used
        account.gift_balance -= gift_used
        account.paid_balance -= paid_used
        account.debt_credits -= repay
        account.total_spent_credits += repay

        revenue = credits_to_cny(repay)
        self.session.add(
            CreditTransaction(
                id=uuid.uuid4(),
                user_id=user_id,
                type="debt_settle",
                credits_delta=-repay,
                paid_delta=-paid_used,
                gift_delta=-gift_used,
                balance_after=self._effective_balance(account),
                ref_type="debt_settle",
                ref_id=ref_id,
                revenue_cny=revenue,
                idempotency_key=idem,
                memo=f"冲抵超长页面待补缴积分 -{repay}",
            )
        )

        lines: list[LedgerLine] = []
        if paid_used > 0:
            paid_face = credits_to_cny(paid_used)
            lines.append(LedgerLine(account_code="2001", debit=paid_face))
            lines.append(LedgerLine(account_code="5001", credit=paid_face))
        if gift_used > 0:
            gift_face = credits_to_cny(gift_used)
            lines.append(LedgerLine(account_code="2002", debit=gift_face))
            lines.append(LedgerLine(account_code="5001", credit=gift_face))
        if lines:
            await self.ledger.post(
                event_type="debt_settle",
                event_ref=idem,
                user_id=user_id,
                memo="冲抵历史超长页面欠费确认收入",
                lines=lines,
            )
        return repay

    # ---------- 充值入账 ----------

    async def _has_other_paid_first_recharge(self, user_id: uuid.UUID, current_order_id: uuid.UUID) -> bool:
        first_keys = list(self.config.first_recharge_only_packages)
        if not first_keys:
            return False
        cnt = await self.session.scalar(
            select(func.count(RechargeOrder.id)).where(
                RechargeOrder.user_id == user_id,
                RechargeOrder.status == "paid",
                RechargeOrder.package_key.in_(first_keys),
                RechargeOrder.id != current_order_id,
            )
        )
        return int(cnt or 0) > 0

    async def apply_recharge(self, order: RechargeOrder) -> bool:
        """订单支付成功后入账，幂等。base→paid 桶，bonus→gift 桶（防多笔首充单重复领赠币），并自动冲抵欠费。"""
        idem = f"recharge:{order.id}"
        if await self._has_transaction(idem):
            return False

        account = await self.get_or_create_account(order.user_id, for_update=True)
        base = int(order.base_credits)
        bonus = int(order.bonus_credits or 0)
        memo = f"充值套餐 {order.package_key}"

        if order.package_key in self.config.first_recharge_only_packages and bonus > 0:
            # 防止先建两笔首充单再先后付款：若已有过充值或另一笔首充单已入账，本单照发基础积分、不再重复发首充赠币。
            already_recharged = account.total_recharged_credits > 0 or await self._has_other_paid_first_recharge(
                order.user_id, order.id
            )
            if already_recharged:
                bonus = 0
                memo = f"充值套餐 {order.package_key}（首充优惠已使用）"

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
                balance_after=self._effective_balance(account),
                ref_type="recharge_order",
                ref_id=str(order.id),
                idempotency_key=idem,
                memo=memo,
            )
        )

        amount = Decimal(str(order.amount_cny))
        cash_account = "1002" if order.payment_provider == "wechat" else "1001"
        lines = [
            LedgerLine(account_code=cash_account, debit=amount),
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

        # 入账后自动冲抵可能存在的超长页面欠费
        await self._settle_debt_from_available(
            account, user_id=order.user_id, ref_id=str(order.id), idem_suffix=f"recharge:{order.id}"
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
        """按账期把基础设施成本（服务器等，非 LLM）入账：借 6002 基础设施成本 / 贷 1102 预付账款。"""
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

    # ---------- 微信资金结算入账（应收第三方 → 现金） ----------

    async def record_wechat_settlement_manual(
        self, settlement_cny: Decimal, fee_cny: Decimal = Decimal("0"), memo: str | None = None
    ) -> None:
        settlement = Decimal(str(settlement_cny))
        fee = Decimal(str(fee_cny or 0))
        if settlement <= 0 and fee <= 0:
            raise BillingError("结算金额与手续费不能都为 0")
        lines = []
        if settlement > 0:
            lines.append(LedgerLine(account_code="1001", debit=settlement))
        if fee > 0:
            lines.append(LedgerLine(account_code="6603", debit=fee))
        lines.append(LedgerLine(account_code="1002", credit=settlement + fee))
        await self.ledger.post(
            event_type="wechat_settlement",
            event_ref=str(uuid.uuid4()),
            memo=memo or "微信结算到账（手动）",
            lines=lines,
        )
        await self.session.flush()

    @staticmethod
    def _wechat_settle_event_ref(bill_date: str) -> str:
        return f"wechat-settle:{bill_date}"

    async def is_wechat_settlement_posted(self, bill_date: str) -> bool:
        from app.models.entities import LedgerEntry

        result = await self.session.execute(
            select(LedgerEntry.id).where(
                LedgerEntry.event_type == "wechat_settlement",
                LedgerEntry.event_ref == self._wechat_settle_event_ref(bill_date),
            )
        )
        return result.scalar_one_or_none() is not None

    async def record_wechat_settlement_from_bill(
        self, bill_date: str, settlement_cny: Decimal, fee_cny: Decimal
    ) -> bool:
        settlement = Decimal(str(settlement_cny))
        fee = Decimal(str(fee_cny or 0))
        if settlement <= 0 and fee <= 0:
            raise BillingError("该账单日无结算金额与手续费可入账")
        lines = []
        if settlement > 0:
            lines.append(LedgerLine(account_code="1001", debit=settlement))
        if fee > 0:
            lines.append(LedgerLine(account_code="6603", debit=fee))
        lines.append(LedgerLine(account_code="1002", credit=settlement + fee))
        entry = await self.ledger.post(
            event_type="wechat_settlement",
            event_ref=self._wechat_settle_event_ref(bill_date),
            memo=f"微信资金账单结算 {bill_date}",
            lines=lines,
        )
        await self.session.flush()
        return entry is not None

    # ---------- 生成前置围栏与动态预授权冻结（Try） ----------

    def hold_for_model(self, model_key: str, *, training_granted: bool) -> int:
        base = self.config.hold_credits_for(model_key)
        if training_granted:
            return discounted_hold_credits(base, self.config.training_discount_rate)
        return base

    def _build_insufficient_suggestions(
        self,
        *,
        available_credits: int,
        is_paid_user: bool,
        has_flagship_trial: bool,
        training_granted: bool,
        requested_models: list[str],
    ) -> dict[str, object]:
        registry = get_model_registry()
        available_keys = {m.key for m in registry.available_models()}

        allowed_pool: list[str]
        if is_paid_user:
            allowed_pool = [k for k in registry.models if k in available_keys]
        elif has_flagship_trial:
            allowed_pool = [
                k
                for k in (*self.config.flagship_trial_allowed_models, *self.config.free_registered_allowed_models)
                if k in available_keys
            ]
        else:
            allowed_pool = [k for k in self.config.free_registered_allowed_models if k in available_keys]

        suggest_single: str | None = None
        candidates = [k for k in requested_models if k in allowed_pool] + [
            k for k in allowed_pool if k not in requested_models
        ]
        # 优先挑当前余额跑得起、质量较高（hold 较大但不超余额）的单模型
        affordable = [
            k for k in candidates if self.hold_for_model(k, training_granted=training_granted) <= available_credits
        ]
        if affordable:
            suggest_single = max(
                affordable, key=lambda k: self.hold_for_model(k, training_granted=training_granted)
            )

        light_pair = [k for k in ["qwen-plus", "doubao-code"] if k in available_keys]
        pair_hold = sum(self.hold_for_model(k, training_granted=training_granted) for k in light_pair)
        suggest_pair = light_pair if len(light_pair) >= 2 and pair_hold <= available_credits else []

        rec_pkg = (
            self.config.first_recharge_only_packages[0]
            if (not is_paid_user and self.config.first_recharge_only_packages)
            else "basic_50"
        )
        return {
            "suggest_single_model": suggest_single,
            "suggest_light_pair": suggest_pair,
            "recommended_package_key": rec_pkg,
            "is_paid_user": is_paid_user,
        }

    async def ensure_can_start_batch(self, user: User, model_keys: list[str]) -> None:
        """兼容无冻结调用的前置校验入口（测试或只读预检用）。实际建批次请调用 reserve_for_batch。"""
        if user.is_anonymous:
            # 行锁持有到批次提交，免费次数与 record_anon_batch_started 的计数在同一事务里串行
            visitor = await self._get_visitor_by_user(user.id, for_update=True)
            used = visitor.free_generations_used if visitor else 0
            if used >= self.config.free_trial_generations:
                raise AnonLimitError(
                    f"免费体验次数已用完，注册即可继续创建并获赠 {self.config.signup_bonus_credits} 积分"
                )
            return

        account = await self.get_or_create_account(user.id)
        if account.debt_credits > 0:
            raise InsufficientCreditsError(
                f"上一轮超长页面待补缴 {account.debt_credits} 积分，充值后将自动抵扣并继续创建",
                code="debt_repayment_required",
                extra={"debt_credits": int(account.debt_credits), "available_credits": int(account.paid_balance + account.gift_balance)},
            )
        if account.paid_balance + account.gift_balance <= 0:
            raise InsufficientCreditsError("积分不足，请先充值后再创建")

    async def reserve_for_batch(
        self,
        *,
        user: User,
        model_keys: list[str],
        training_granted: bool,
        has_documents: bool = False,
    ) -> BatchReservation:
        """提交批次时加行锁校验模型权限、欠费与可用余额，并按模型拆分冻结积分。"""
        if user.is_anonymous:
            await self.ensure_can_start_batch(user, model_keys)
            return BatchReservation(
                used_flagship_trial=False,
                allocations=[TaskHoldAllocation(key, 0, 0) for key in model_keys],
            )

        account = await self.get_or_create_account(user.id, for_update=True)
        is_paid_user = account.total_recharged_credits > 0
        has_trial = account.free_flagship_batches_remaining > 0
        available = int(account.paid_balance) + int(account.gift_balance)

        if account.debt_credits > 0:
            suggestions = self._build_insufficient_suggestions(
                available_credits=available,
                is_paid_user=is_paid_user,
                has_flagship_trial=has_trial,
                training_granted=training_granted,
                requested_models=model_keys,
            )
            raise InsufficientCreditsError(
                f"账号有 {account.debt_credits} 待补缴积分，充值后自动抵扣即可继续创建",
                code="debt_repayment_required",
                extra={
                    "available_credits": available,
                    "required_hold_credits": int(account.debt_credits),
                    "debt_credits": int(account.debt_credits),
                    **suggestions,
                },
            )

        # 未充值注册用户的旗舰模型围栏：轻量模型随时可用；主力双模（qwen/doubao）可用 1 次首秀券；其余旗舰充值解锁
        used_flagship_trial = False
        if not is_paid_user:
            non_light = [k for k in model_keys if not self.config.is_free_registered_allowed_model(k)]
            if non_light:
                can_use_trial = has_trial and all(self.config.is_flagship_trial_model(k) for k in non_light)
                if not can_use_trial:
                    suggestions = self._build_insufficient_suggestions(
                        available_credits=available,
                        is_paid_user=False,
                        has_flagship_trial=has_trial,
                        training_granted=training_granted,
                        requested_models=model_keys,
                    )
                    msg = (
                        "所选顶配模型需首充解锁，也可切换轻量模型继续使用赠送积分"
                        if has_trial
                        else "免费旗舰首秀已体验，首充即可解锁全部旗舰模型，或切换轻量模型继续创建"
                    )
                    raise FlagshipUpgradeRequiredError(
                        msg,
                        extra={
                            "available_credits": available,
                            "required_hold_credits": sum(
                                self.hold_for_model(k, training_granted=training_granted) for k in model_keys
                            ),
                            "debt_credits": 0,
                            "locked_models": non_light,
                            **suggestions,
                        },
                    )
                used_flagship_trial = True

        per_task_holds = [self.hold_for_model(k, training_granted=training_granted) for k in model_keys]
        if has_documents and per_task_holds and self.config.compression_hold_credits > 0:
            doc_extra = self.config.compression_hold_credits
            if training_granted:
                doc_extra = discounted_hold_credits(doc_extra, self.config.training_discount_rate)
            # 均摊到各任务上（余数加在第一个任务），单任务失败或取消时也能独立精确解冻
            share, rem = divmod(doc_extra, len(per_task_holds))
            per_task_holds = [h + share + (1 if idx < rem else 0) for idx, h in enumerate(per_task_holds)]

        required_total = sum(per_task_holds)
        if available < required_total:
            suggestions = self._build_insufficient_suggestions(
                available_credits=available,
                is_paid_user=is_paid_user,
                has_flagship_trial=has_trial,
                training_granted=training_granted,
                requested_models=model_keys,
            )
            raise InsufficientCreditsError(
                f"当前可用 {available} 积分，运行所选模型需预留 {required_total} 积分（生成后按实际消耗多退）",
                extra={
                    "available_credits": available,
                    "required_hold_credits": required_total,
                    "debt_credits": 0,
                    **suggestions,
                },
            )

        allocations: list[TaskHoldAllocation] = []
        for key, hold in zip(model_keys, per_task_holds, strict=True):
            gift_take = min(int(account.gift_balance), hold)
            paid_take = hold - gift_take
            account.gift_balance -= gift_take
            account.paid_balance -= paid_take
            account.frozen_gift_balance += gift_take
            account.frozen_paid_balance += paid_take
            allocations.append(TaskHoldAllocation(model_key=key, reserved_gift=gift_take, reserved_paid=paid_take))

        if used_flagship_trial:
            account.free_flagship_batches_remaining = max(0, int(account.free_flagship_batches_remaining) - 1)

        await self.session.flush()
        return BatchReservation(used_flagship_trial=used_flagship_trial, allocations=allocations)

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

    async def refund_anon_batch_started(self, user: User) -> None:
        """资料阶段整批失败时退回这一次匿名免费额度。"""
        if not user.is_anonymous:
            return
        visitor = await self._get_visitor_by_user(user.id)
        if visitor is not None and visitor.free_generations_used > 0:
            visitor.free_generations_used -= 1
        account = await self.get_or_create_account(user.id)
        if account.free_generations_used > 0:
            account.free_generations_used -= 1
        await self.session.flush()

    async def refund_flagship_trial_if_batch_all_failed(self, batch_id: uuid.UUID | None) -> bool:
        """使用了首秀券的批次若全部任务均失败/未产出任何版本，幂等退回 1 次首秀券。"""
        if batch_id is None:
            return False
        batch = await self.session.get(GenerationBatch, batch_id)
        if batch is None or not batch.used_flagship_trial or batch.flagship_trial_refunded:
            return False
        rows = (
            await self.session.execute(
                select(GenerationTask.status).where(GenerationTask.batch_id == batch_id)
            )
        ).scalars().all()
        if not rows or any(s in ("pending", "running", "succeeded") for s in rows):
            return False

        claimed = (
            await self.session.execute(
                update(GenerationBatch)
                .where(
                    GenerationBatch.id == batch_id,
                    GenerationBatch.used_flagship_trial.is_(True),
                    GenerationBatch.flagship_trial_refunded.is_(False),
                )
                .values(flagship_trial_refunded=True)
                .returning(GenerationBatch.conversation_id)
            )
        ).scalar_one_or_none()
        if claimed is None:
            return False
        from app.models.entities import Conversation

        conv = await self.session.get(Conversation, claimed)
        if conv is None:
            return False
        account = await self.get_or_create_account(conv.owner_user_id, for_update=True)
        if account.total_recharged_credits == 0:
            account.free_flagship_batches_remaining += 1
        await self.session.flush()
        return True

    async def _get_visitor_by_user(self, user_id: uuid.UUID, *, for_update: bool = False) -> AnonVisitor | None:
        stmt = select(AnonVisitor).where(AnonVisitor.user_id == user_id)
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ---------- 账户摘要 / 充值订单 ----------

    async def account_summary(self, user: User | None) -> AccountSummary:
        """供 /api/billing/account 使用。paid/gift/total_balance 返回当前可用余额。"""
        limit = self.config.free_trial_generations
        if user is None:
            return AccountSummary(True, 0, 0, 0, 0, limit, limit, False)

        if user.is_anonymous:
            visitor = await self._get_visitor_by_user(user.id)
            used = visitor.free_generations_used if visitor else 0
            return AccountSummary(True, 0, 0, 0, used, limit, max(limit - used, 0), False)

        account = await self.get_or_create_account(user.id)
        await self.session.commit()
        paid = int(account.paid_balance)
        gift = int(account.gift_balance)
        frozen = int(account.frozen_paid_balance) + int(account.frozen_gift_balance)
        is_paid = int(account.total_recharged_credits) > 0
        return AccountSummary(
            is_anonymous=False,
            paid_balance=paid,
            gift_balance=gift,
            total_balance=paid + gift,
            free_generations_used=int(account.free_generations_used),
            free_generations_limit=limit,
            free_generations_remaining=0,
            signup_bonus_granted=bool(account.signup_bonus_granted),
            frozen_balance=frozen,
            debt_credits=int(account.debt_credits),
            is_paid_user=is_paid,
            free_flagship_batches_remaining=0 if is_paid else int(account.free_flagship_batches_remaining),
        )

    async def list_active_packages(self, user: User | None = None) -> list[CreditPackage]:
        result = await self.session.execute(
            select(CreditPackage).where(CreditPackage.is_active.is_(True)).order_by(CreditPackage.sort_order.asc())
        )
        all_pkgs = list(result.scalars().all())

        is_paid_user = False
        if user is not None and not user.is_anonymous:
            account = await self.session.get(CreditAccount, user.id)
            is_paid_user = bool(account and account.total_recharged_credits > 0)

        first_only = set(self.config.first_recharge_only_packages)
        hide_on_first = set(self.config.hide_when_first_recharge_available)
        if is_paid_user:
            return [p for p in all_pkgs if p.key not in first_only]
        return [p for p in all_pkgs if p.key not in hide_on_first]

    async def create_recharge_order(
        self, user: User, package_key: str, *, provider: str = "mock"
    ) -> RechargeOrder:
        """建单：金额/积分一律服务端按套餐计算，并校验首充包资格。"""
        package = await self.session.get(CreditPackage, package_key)
        if package is None or not package.is_active:
            raise BillingError("套餐不存在或已下架")

        if package.key in self.config.first_recharge_only_packages:
            account = await self.get_or_create_account(user.id)
            if account.total_recharged_credits > 0:
                raise BillingError("首充体验包仅限首次充值购买，请选择其他套餐", code="first_recharge_already_used")

        amount = Decimal(str(package.amount_cny))
        if amount < Decimal(str(self.config.min_recharge_cny)) or amount > Decimal(str(self.config.max_recharge_cny)):
            raise BillingError("充值金额超出允许范围")

        order_id = uuid.uuid4()
        order = RechargeOrder(
            id=order_id,
            user_id=user.id,
            package_key=package.key,
            amount_cny=package.amount_cny,
            base_credits=package.base_credits,
            bonus_credits=package.bonus_credits,
            status="pending",
            payment_provider=provider,
            out_trade_no=order_id.hex,
        )
        self.session.add(order)
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def mark_order_paid(self, order_id: uuid.UUID, *, provider_txn_id: str | None = None) -> RechargeOrder:
        """原子流转 pending→paid（防重复回调），随后幂等入账并冲抵欠费。"""
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

    # ---------- 预授权释放与取消结算（Cancel） ----------

    async def release_task_reservation(self, task: GenerationTask) -> int:
        """全额退回该任务冻结的积分（报错失败、首字前取消、启动扫尾标失败调用）。按 reservation_settled 幂等。"""
        if task.reservation_settled:
            return 0
        res_gift = int(task.reserved_gift_credits or 0)
        res_paid = int(task.reserved_paid_credits or 0)
        task.reservation_settled = True
        if res_gift <= 0 and res_paid <= 0:
            await self.session.flush()
            return 0

        account = await self.get_or_create_account(task.requested_by_user_id, for_update=True)
        unfreeze_gift = min(int(account.frozen_gift_balance), res_gift)
        unfreeze_paid = min(int(account.frozen_paid_balance), res_paid)
        account.frozen_gift_balance -= unfreeze_gift
        account.frozen_paid_balance -= unfreeze_paid
        account.gift_balance += unfreeze_gift
        account.paid_balance += unfreeze_paid
        await self.session.flush()
        return unfreeze_gift + unfreeze_paid

    async def settle_cancelled_task(
        self,
        *,
        task: GenerationTask,
        user: User,
        estimated_output_tokens: int,
        raw_cost_cny: Decimal | float | None,
    ) -> SettleResult:
        """用户主动点击停止时的结算：
        - 未超过阈值（默认 2000 tokens）或匿名：全额解冻退回（匿名若已有真实成本仍记 6001 COGS）；
        - 已超过阈值：按 1.0x 原始成本保底扣费（不高于本任务预留额），其余冻结原路退回。
        """
        idem = f"consume:cancel:{task.id}"
        raw_cost = Decimal(str(raw_cost_cny)) if raw_cost_cny is not None else Decimal("0")
        if raw_cost < 0:
            raw_cost = Decimal("0")

        if await self._has_transaction(idem):
            if not task.reservation_settled:
                task.reservation_settled = True
            return SettleResult(0, 0, 0, 0, already_settled=True)

        if user.is_anonymous:
            task.reservation_settled = True
            if raw_cost > 0:
                await self._settle_anonymous(
                    user, task.id, idem, task.model_key, raw_cost, 1.0, ref_type="cancelled_task"
                )
            return SettleResult(0, 0, 0, 0)

        if estimated_output_tokens < self.config.cancel_charge_min_output_tokens or raw_cost <= 0:
            refunded = await self.release_task_reservation(task)
            if raw_cost > 0:
                await self._post_cogs(task.id, idem, user.id, raw_cost, memo="中途停止未达起扣线算力损耗")
            return SettleResult(0, 0, 0, 0, reserved_credits=refunded, refunded_credits=refunded)

        base_charge = credits_for_cost(raw_cost, self.config.cancel_cost_multiplier)
        return await self._consume_with_task_reservation(
            user=user,
            task=task,
            idem=idem,
            ref_type="cancelled_task",
            ref_id=str(task.id),
            model_key=task.model_key,
            billable_credits=base_charge,
            discount_credits=0,
            cogs_cny=raw_cost,
            markup_snapshot=self.config.cancel_cost_multiplier,
            memo="中途停止 · 按已生成进度结算基础算力",
            allow_debt=False,
        )

    # ---------- 生成完成结算（Confirm） ----------

    async def settle_generation(
        self,
        *,
        user: User,
        version_id: uuid.UUID,
        model_key: str | None,
        raw_cost_cny: Decimal | float | None,
        markup: float,
        training_granted: bool = False,
        task: GenerationTask | None = None,
        ledger_cogs_cny: Decimal | float | None = None,
    ) -> SettleResult:
        """按页面版本结算一次生成消耗，按 version_id 幂等。

        raw_cost_cny：向用户计价的原始成本（如发生 Tailwind 格式违规重跑，仅传成功轮成本）；
        ledger_cogs_cny：记入 6001/1102 的真实总算力成本（默认等于 raw_cost_cny；重跑时传双轮之和）。
        """
        idem = f"consume:{version_id}"
        raw_cost = Decimal(str(raw_cost_cny)) if raw_cost_cny is not None else Decimal("0")
        if raw_cost < 0:
            raw_cost = Decimal("0")
        cogs_cost = Decimal(str(ledger_cogs_cny)) if ledger_cogs_cny is not None else raw_cost
        if cogs_cost < raw_cost:
            cogs_cost = raw_cost

        if await self._has_transaction(idem):
            if task is not None and not task.reservation_settled:
                task.reservation_settled = True
            return SettleResult(0, 0, 0, 0, already_settled=True)

        if user.is_anonymous:
            if task is not None:
                task.reservation_settled = True
            return await self._settle_anonymous(user, version_id, idem, model_key, cogs_cost, markup)

        standard_credits = credits_for_cost(raw_cost, markup)
        credits = (
            apply_training_discount(
                standard_credits,
                self.config.training_discount_rate,
                raw_cost_cny=raw_cost,
                floor_multiplier=self.config.cost_floor_multiplier,
            )
            if training_granted
            else standard_credits
        )
        discount_credits = max(standard_credits - credits, 0)
        memo = f"生成消耗 · 授权优惠 -{discount_credits}" if discount_credits > 0 else "生成消耗"

        return await self._consume_with_task_reservation(
            user=user,
            task=task,
            idem=idem,
            ref_type="page_version",
            ref_id=str(version_id),
            model_key=model_key,
            billable_credits=credits,
            discount_credits=discount_credits,
            cogs_cny=cogs_cost,
            markup_snapshot=markup,
            memo=memo,
            allow_debt=True,
        )

    async def _settle_anonymous(
        self,
        user: User,
        ref_uuid: uuid.UUID,
        idem: str,
        model_key: str | None,
        raw_cost: Decimal,
        markup: float,
        *,
        ref_type: str = "page_version",
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
                ref_type=ref_type,
                ref_id=str(ref_uuid),
                model_key=model_key,
                raw_cost_cny=raw_cost,
                markup=Decimal(str(markup)),
                revenue_cny=Decimal("0"),
                idempotency_key=idem,
                memo="匿名试用消耗（无收入）",
            )
        )
        await self._post_cogs(ref_uuid, idem, user.id, raw_cost, memo="匿名试用算力成本")
        await self.session.flush()
        return SettleResult(0, 0, 0, 0)

    async def _consume_with_task_reservation(
        self,
        *,
        user: User,
        task: GenerationTask | None,
        idem: str,
        ref_type: str,
        ref_id: str,
        model_key: str | None,
        billable_credits: int,
        discount_credits: int,
        cogs_cny: Decimal,
        markup_snapshot: float,
        memo: str,
        allow_debt: bool,
    ) -> SettleResult:
        account = await self.get_or_create_account(user.id, for_update=True)

        res_gift = 0
        res_paid = 0
        has_reservation = False
        if task is not None and not task.reservation_settled:
            res_gift = min(int(account.frozen_gift_balance), int(task.reserved_gift_credits or 0))
            res_paid = min(int(account.frozen_paid_balance), int(task.reserved_paid_credits or 0))
            has_reservation = (int(task.reserved_gift_credits or 0) + int(task.reserved_paid_credits or 0)) > 0
            task.reservation_settled = True

        reserved_total = res_gift + res_paid
        # 先把本任务的冻结解回可用池，再统一按先 gift 后 paid 扣实收积分；多余部分自然留在可用池即完成多退。
        account.frozen_gift_balance -= res_gift
        account.frozen_paid_balance -= res_paid
        account.gift_balance += res_gift
        account.paid_balance += res_paid

        if not allow_debt and has_reservation:
            # 中途停止保底费绝不超过该任务已冻结额度
            target = min(billable_credits, reserved_total)
        else:
            target = billable_credits

        available_now = int(account.gift_balance) + int(account.paid_balance)
        deduct = min(target, available_now)
        gift_used = min(int(account.gift_balance), deduct)
        paid_used = deduct - gift_used

        account.gift_balance -= gift_used
        account.paid_balance -= paid_used
        account.total_spent_credits += deduct

        shortfall = max(target - deduct, 0)
        debt_added = 0
        # 仅对有预授权的新任务在超出预留且可用余额不足时记入 ≤100 欠费；迁移瞬间无预留的在途老任务维持扣到 0 为止。
        if shortfall > 0 and allow_debt and has_reservation and self.config.max_debt_credits_per_task > 0:
            room = max(0, self.config.max_debt_credits_per_task)
            debt_added = min(shortfall, room)
            account.debt_credits += debt_added
            if debt_added > 0:
                memo = f"{memo}（超长页面待补缴 {debt_added} 积分）"

        refunded_credits = max(reserved_total - deduct, 0)
        balance_after = self._effective_balance(account)
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
                ref_type=ref_type,
                ref_id=ref_id,
                model_key=model_key,
                raw_cost_cny=cogs_cny,
                markup=Decimal(str(markup_snapshot)),
                revenue_cny=revenue,
                training_discount_credits=discount_credits,
                idempotency_key=idem,
                memo=memo,
            )
        )

        lines: list[LedgerLine] = []
        if paid_used > 0:
            paid_face = credits_to_cny(paid_used)
            lines.append(LedgerLine(account_code="2001", debit=paid_face))
            lines.append(LedgerLine(account_code="5001", credit=paid_face))
        if gift_used > 0:
            gift_face = credits_to_cny(gift_used)
            lines.append(LedgerLine(account_code="2002", debit=gift_face))
            lines.append(LedgerLine(account_code="5001", credit=gift_face))
        if cogs_ny := cogs_cny:
            if cogs_ny > 0:
                lines.append(LedgerLine(account_code="6001", debit=cogs_ny))
                lines.append(LedgerLine(account_code="1102", credit=cogs_ny))

        if lines:
            await self.ledger.post(
                event_type="consume",
                event_ref=idem,
                user_id=user.id,
                memo="生成消耗确认收入与成本",
                lines=lines,
            )
        await self.session.flush()
        return SettleResult(
            credits_charged=deduct,
            paid_used=paid_used,
            gift_used=gift_used,
            balance_after=balance_after,
            discount_credits=discount_credits,
            reserved_credits=reserved_total,
            refunded_credits=refunded_credits,
            debt_added=debt_added,
        )

    async def _post_cogs(
        self, ref_id: uuid.UUID, idem: str, user_id: uuid.UUID, raw_cost: Decimal, *, memo: str
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
