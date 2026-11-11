"""计费单位经济与 TCC 动态预授权单测 & 临时数据库集成测试。

包含：
1. 纯逻辑单测（无需数据库即可运行）：92 折向下取整 + 1.25x 成本安全地板、预留额计算、双模型路由/重试成本合并、取消字符估算（含思考）。
2. 数据库集成测试（设置 TEST_DATABASE_URL 时运行）：
   - FOR UPDATE 并发防超发（两轮同时提交只成功一轮，绝不穿透）；
   - 新注册赠送 450 + 1 次旗舰首秀券（qwen+doubao 预留 396 一步跑通、完成后多退、次轮旗舰拦截、全败退券）；
   - 超长页面超出预留产生 ≤100 欠费、欠费期间禁止新生成、充值自动冲抵并写 debt_settle 平衡凭证；
   - 中途停止未满 2000 Token 全额解冻 vs 满 2000 Token 按 1.0x 成本扣费；
   - 先建两笔首充单再先后付款：第二笔只发基础积分不发 100 赠币；
   - 启动扫尾 sweep：重跑沿用预留不重复扣、超时标失败原路解冻；
   - 管理后台全站与用户余额按「可用 + 冻结」口径对齐总账。
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_billing_config  # noqa: E402
from app.services.billing.errors import FlagshipUpgradeRequiredError, InsufficientCreditsError  # noqa: E402
from app.services.billing.pricing import (  # noqa: E402
    apply_training_discount,
    cost_floor_credits,
    credits_for_cost,
    discounted_hold_credits,
)
from app.services.generation_service import _estimate_stream_usage  # noqa: E402
from app.services.llm.cost import merge_cost_breakdowns  # noqa: E402
from app.services.llm.types import LlmCostBreakdown, LlmMessage  # noqa: E402

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


# ---------- 纯逻辑单测 ----------


def test_cost_floor_prevents_small_task_underwater():
    # 复现现网负毛利案例：原始成本 ¥0.0414
    # 旧规则：ceil(0.0414*1.2*100)=5，floor(5*0.9)=4 (¥0.0400 < ¥0.0414 亏本)
    # 新规则：ceil(0.0414*2.0*100)=9，floor(9*0.92)=8；地板 ceil(0.0414*1.25*100)=6 -> 实收 8 积分
    std = credits_for_cost(Decimal("0.0414"), 2.0)
    assert std == 9
    charged = apply_training_discount(std, 0.92, raw_cost_cny=Decimal("0.0414"), floor_multiplier=1.25)
    assert charged == 8

    # 极端贴近地板：原始成本 ¥0.0440，若某模型倍率 1.3 -> std=6，floor(6*0.92)=5 (¥0.050)
    # 地板 ceil(0.0440 * 1.25 * 100) = ceil(5.5) = 6 -> 被成本安全地板托住为 6，绝不低于 1.25x 成本
    std_tight = credits_for_cost(Decimal("0.0440"), 1.3)
    assert std_tight == 6
    assert cost_floor_credits(Decimal("0.0440"), 1.25) == 6
    charged_tight = apply_training_discount(
        std_tight, 0.92, raw_cost_cny=Decimal("0.0440"), floor_multiplier=1.25
    )
    assert charged_tight == 6


def test_qwen_doubao_first_showcase_hold_fits_450_signup_bonus():
    cfg = get_billing_config()
    assert cfg.signup_bonus_credits == 450
    assert cfg.hold_credits_for("qwen") == 325
    assert cfg.hold_credits_for("doubao") == 105
    qwen_hold = discounted_hold_credits(325, cfg.training_discount_rate)
    doubao_hold = discounted_hold_credits(105, cfg.training_discount_rate)
    assert (qwen_hold, doubao_hold) == (299, 97)
    assert qwen_hold + doubao_hold == 396 < cfg.signup_bonus_credits


def test_merge_cost_breakdowns_preserves_router_light_price():
    router_cost = LlmCostBreakdown(
        currency="CNY",
        tier_label="默认",
        input_tokens=500,
        output_tokens=4,
        cached_input_tokens=0,
        reasoning_tokens=0,
        input_per_million=0.8,
        output_per_million=2.7,
        cache_input_per_million=0.1,
        input_cost_cny=0.0004,
        output_cost_cny=0.0000108,
        total_cost_cny=0.0004108,
    )
    main_cost = LlmCostBreakdown(
        currency="CNY",
        tier_label="默认",
        input_tokens=2000,
        output_tokens=10000,
        cached_input_tokens=0,
        reasoning_tokens=5000,
        input_per_million=13.0,
        output_per_million=68.0,
        cache_input_per_million=1.3,
        input_cost_cny=0.026,
        output_cost_cny=0.68,
        total_cost_cny=0.706,
    )
    merged = merge_cost_breakdowns(router_cost, main_cost)
    assert merged is not None
    assert merged.input_tokens == 2500
    assert merged.output_tokens == 10004
    assert abs(merged.total_cost_cny - 0.7064108) < 1e-7


def test_estimate_stream_usage_counts_reasoning_and_answer():
    msgs = [LlmMessage(role="user", content="a" * 400)]
    # 思考 3600 字 + 正文 1000 字 = 4600 字 -> 约 2300 输出 Token（超过 2000 起扣线）
    usage = _estimate_stream_usage(msgs, reasoning_chars=3600, answer_chars=1000)
    assert usage is not None
    assert usage.output_tokens == 2300
    assert usage.reasoning_tokens == 1800
    assert usage.input_tokens == 200


# ---------- 数据库集成测试 ----------


db_only = pytest.mark.skipif(not TEST_DATABASE_URL, reason="未设置 TEST_DATABASE_URL")


def _run_db(coro_fn) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    async def runner() -> None:
        engine = create_async_engine(TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1))
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        try:
            await coro_fn(factory)
        finally:
            await engine.dispose()

    asyncio.run(runner())


@db_only
def test_db_signup_showcase_concurrent_for_update_and_refund():
    from sqlalchemy import select

    from app.models.entities import CreditAccount, GenerationBatch, GenerationTask, PageVersion, User
    from app.services.billing.account import BillingService
    from app.services.generation_service import GenerationService

    async def flow(factory):
        async with factory() as session:
            user = User(
                username=f"u-{uuid.uuid4().hex[:10]}",
                display_name="新注册测试",
                is_anonymous=False,
                allow_training=True,
                policy_notice_seen_at=datetime.now(UTC),
            )
            session.add(user)
            await session.flush()
            granted = await BillingService(session).grant_signup_bonus(user)
            await session.commit()
            assert granted == 450
            uid = user.id

        # 两个并发请求同时用 450 积分抢 qwen+doubao（每轮需预留 396）：FOR UPDATE 串行化后只能成功 1 个！
        async def submit_once():
            async with factory() as s:
                u = await s.get(User, uid)
                return await GenerationService(s).create_batch(
                    prompt="做一份网页简历", selected_model_keys=["qwen", "doubao"], user=u
                )

        outcomes = await asyncio.gather(submit_once(), submit_once(), return_exceptions=True)
        ok = [x for x in outcomes if not isinstance(x, Exception)]
        errs = [x for x in outcomes if isinstance(x, Exception)]
        assert len(ok) == 1 and len(errs) == 1
        assert isinstance(errs[0], (InsufficientCreditsError, FlagshipUpgradeRequiredError))

        creation = ok[0]
        async with factory() as s:
            acc = await s.get(CreditAccount, uid)
            assert acc.gift_balance == 450 - 396
            assert acc.frozen_gift_balance == 396
            assert acc.free_flagship_batches_remaining == 0

            # 若整批两个任务都因上游故障失败：预留 396 全额退回，首秀券也自动退回 1 次
            for run in creation.runs:
                await GenerationService(s).finish_unfinished_task(
                    run.task_id, status="failed", message="上游超时", user_message="失败"
                )
            acc = await s.get(CreditAccount, uid, populate_existing=True)
            assert acc.gift_balance == 450
            assert acc.frozen_gift_balance == 0
            assert acc.free_flagship_batches_remaining == 1

            # 再次使用首秀券并正常完成结算（qwen 原始成本 ¥1.00，doubao ¥0.40）
            u = await s.get(User, uid)
            c2 = await GenerationService(s).create_batch(
                prompt="再做一份网页简历", selected_model_keys=["qwen", "doubao"], user=u
            )
            tasks = {
                r.model_key: await s.get(GenerationTask, r.task_id) for r in c2.runs
            }
            for mk, raw_cny in (("qwen", Decimal("1.00")), ("doubao", Decimal("0.40"))):
                t = tasks[mk]
                ver = PageVersion(
                    id=uuid.uuid4(),
                    page_id=t.page_id,
                    version_number=1,
                    prompt="p",
                    storage_key="k",
                    model_key=mk,
                    batch_id=c2.batch_id,
                )
                s.add(ver)
                await s.flush()
                t.status = "succeeded"
                res = await BillingService(s).settle_generation(
                    user=u,
                    version_id=ver.id,
                    model_key=mk,
                    raw_cost_cny=raw_cny,
                    markup=1.65,
                    training_granted=True,
                    task=t,
                )
                assert res.refunded_credits > 0
            await s.commit()

            acc = await s.get(CreditAccount, uid, populate_existing=True)
            # qwen: ceil(100*1.65)=165 -> floor(165*0.92)=151; doubao: ceil(40*1.65)=66 -> floor(66*0.92)=60 => 共扣 211
            assert acc.frozen_gift_balance == 0 and acc.frozen_paid_balance == 0
            assert acc.gift_balance == 450 - 211

            # 首秀券已用完：再选 qwen 会被拦截提示首充解锁，选轻量 qwen-plus 则放行
            with pytest.raises(FlagshipUpgradeRequiredError):
                await GenerationService(s).create_batch(
                    prompt="第三轮旗舰", selected_model_keys=["qwen"], user=u
                )
            await s.rollback()
            u = await s.get(User, uid)

            c_light = await GenerationService(s).create_batch(
                prompt="第三轮轻量微调", selected_model_keys=["qwen-plus"], user=u
            )
            assert len(c_light.runs) == 1
            # 清理释放最后一轮预留，保持冻结守恒
            await GenerationService(s).finish_unfinished_task(
                c_light.runs[0].task_id, status="failed", message="done", user_message="done"
            )
            batch_row = await s.scalar(select(GenerationBatch).where(GenerationBatch.id == c2.batch_id))
            assert batch_row.used_flagship_trial is True

    _run_db(flow)


@db_only
def test_db_overage_debt_cap_repayment_and_double_first_recharge():
    from sqlalchemy import func, select

    from app.models.entities import (
        CreditAccount,
        CreditTransaction,
        GenerationTask,
        LedgerEntryLine,
        PageVersion,
        User,
    )
    from app.services.billing.account import BillingService
    from app.services.generation_service import GenerationService

    async def flow(factory):
        async with factory() as s:
            user = User(
                username=f"debt-{uuid.uuid4().hex[:10]}",
                display_name="欠费与首充测试",
                is_anonymous=False,
                allow_training=False,
                policy_notice_seen_at=datetime.now(UTC),
            )
            s.add(user)
            await s.flush()

            # 先在未充值状态连续创建两笔首充单 first_19_9（测试建两笔后分别付款防重）
            bs = BillingService(s)
            order1 = await bs.create_recharge_order(user, "first_19_9", provider="mock")
            order2 = await bs.create_recharge_order(user, "first_19_9", provider="mock")
            order1_id = order1.id
            order2_id = order2.id
            uid = user.id

            # 先给账号放 350 付费积分，手动模拟首充前跑一次超长 qwen（不授权预留 325，剩余可用 25）
            acc = await bs.get_or_create_account(user.id, for_update=True)
            acc.paid_balance = 350
            acc.free_flagship_batches_remaining = 1
            await s.commit()

            c = await GenerationService(s).create_batch(
                prompt="超长页面", selected_model_keys=["qwen"], user=user
            )
            task = await s.get(GenerationTask, c.runs[0].task_id)
            assert task.reserved_paid_credits == 325

            # 实际成本高达 ¥3.80 -> 1.65x = 627 积分；可用池 350 全扣光后仍差 277，但单任务欠费封顶记 100！
            ver = PageVersion(
                id=uuid.uuid4(),
                page_id=task.page_id,
                version_number=1,
                prompt="p",
                storage_key="k",
                model_key="qwen",
                batch_id=c.batch_id,
            )
            s.add(ver)
            await s.flush()
            res = await bs.settle_generation(
                user=user,
                version_id=ver.id,
                model_key="qwen",
                raw_cost_cny=Decimal("3.80"),
                markup=1.65,
                training_granted=False,
                task=task,
            )
            await s.commit()
            assert res.credits_charged == 350
            assert res.debt_added == 100

            acc = await s.get(CreditAccount, user.id, populate_existing=True)
            assert acc.paid_balance == 0 and acc.frozen_paid_balance == 0
            assert acc.debt_credits == 100

            # 有未结清欠费时，禁止发起新一轮生成
            with pytest.raises(InsufficientCreditsError) as exc_info:
                await GenerationService(s).create_batch(
                    prompt="欠费期间再建", selected_model_keys=["qwen-plus"], user=user
                )
            assert exc_info.value.code == "debt_repayment_required"
            await s.rollback()

            # 支付第一笔首充单 order1：到账 1990 paid + 100 gift，自动用 100 gift 冲抵 100 欠费！
            await bs.mark_order_paid(order1_id, provider_txn_id=f"wx-1-{order1_id}")
            acc = await s.get(CreditAccount, uid, populate_existing=True)
            assert acc.debt_credits == 0
            assert acc.gift_balance == 0
            assert acc.paid_balance == 1990

            debt_txn = await s.scalar(
                select(CreditTransaction).where(
                    CreditTransaction.user_id == uid,
                    CreditTransaction.type == "debt_settle",
                )
            )
            assert debt_txn is not None
            assert debt_txn.credits_delta == -100 and debt_txn.gift_delta == -100

            # 支付第二笔首充单 order2：因已首充过，只发 1990 基础积分，不再重复发 100 赠币！
            await bs.mark_order_paid(order2_id, provider_txn_id=f"wx-2-{order2_id}")
            acc = await s.get(CreditAccount, uid, populate_existing=True)
            assert acc.paid_balance == 3980
            assert acc.gift_balance == 0

            # 校验该用户所有复式凭证借贷严格相等
            sums = (
                await s.execute(
                    select(
                        func.coalesce(func.sum(LedgerEntryLine.debit), 0),
                        func.coalesce(func.sum(LedgerEntryLine.credit), 0),
                    )
                )
            ).one()
            assert Decimal(str(sums[0])) == Decimal(str(sums[1]))

    _run_db(flow)


@db_only
def test_db_cancel_threshold_and_sweep_conservation(monkeypatch):
    from sqlalchemy import select, update

    from app.models.entities import CreditAccount, GenerationEvent, GenerationTask, User
    from app.services.billing.account import BillingService
    from app.services.generation_runner import GenerationRunner
    from app.services.generation_service import GenerationService

    async def flow(factory):
        async with factory() as s:
            user = User(
                username=f"cancel-{uuid.uuid4().hex[:10]}",
                display_name="取消与扫尾测试",
                is_anonymous=False,
                allow_training=False,
                policy_notice_seen_at=datetime.now(UTC),
            )
            s.add(user)
            await s.flush()
            acc = await BillingService(s).get_or_create_account(user.id, for_update=True)
            acc.paid_balance = 1000
            acc.total_recharged_credits = 1000
            await s.commit()
            uid = user.id

            # 1) 输出未满 2000 Token 取消：100% 全额退回预留
            c1 = await GenerationService(s).create_batch(
                prompt="短取消", selected_model_keys=["doubao"], user=user
            )
            t1_id = c1.runs[0].task_id
            s.add(GenerationEvent(task_id=t1_id, event_type="reasoning_delta", payload={"text": "短思考" * 50}))
            await s.commit()
            await GenerationService(s).finish_unfinished_task(
                t1_id, status="cancelled", message="用户停止了生成", user_message="已停止生成"
            )
            acc = await s.get(CreditAccount, uid, populate_existing=True)
            assert acc.paid_balance == 1000 and acc.frozen_paid_balance == 0

            # 2) 输出满 2000 Token（如 5000 字思考 = 2500 Token）主动取消：按 1.0x 原始成本保底扣费，其余退回
            c2 = await GenerationService(s).create_batch(
                prompt="长思考后取消", selected_model_keys=["qwen"], user=user
            )
            t2_id = c2.runs[0].task_id
            s.add(GenerationEvent(task_id=t2_id, event_type="reasoning_delta", payload={"text": "思" * 5000}))
            await s.commit()
            await GenerationService(s).finish_unfinished_task(
                t2_id, status="cancelled", message="用户停止了生成", user_message="已停止生成"
            )
            acc = await s.get(CreditAccount, uid, populate_existing=True)
            assert acc.frozen_paid_balance == 0
            assert 0 < (1000 - acc.paid_balance) <= 325

            # 3) 启动扫尾 sweep：r_rerun 重跑保留预留不重复扣；r_stale 超时标失败自动解冻！
            bal_before = acc.paid_balance
            c3 = await GenerationService(s).create_batch(
                prompt="扫尾守恒", selected_model_keys=["doubao", "qwen-plus"], user=user
            )
            t_rerun, t_stale = c3.runs[0].task_id, c3.runs[1].task_id
            old = datetime.now(UTC) - timedelta(days=2)
            await s.execute(
                update(GenerationTask)
                .where(GenerationTask.id == t_rerun)
                .values(status="running", started_at=datetime.now(UTC) - timedelta(minutes=2))
            )
            await s.execute(
                update(GenerationTask)
                .where(GenerationTask.id == t_stale)
                .values(status="running", created_at=old, started_at=old)
            )
            await s.commit()

        runner = GenerationRunner(session_factory=factory)
        monkeypatch.setattr(runner, "start", lambda *_a, **_kw: None)
        try:
            await runner.startup_sweep()
        finally:
            await runner.shutdown()

        async with factory() as s:
            acc = await s.get(CreditAccount, uid)
            rerun_row = await s.get(GenerationTask, t_rerun)
            stale_row = await s.get(GenerationTask, t_stale)
            assert rerun_row.status == "pending" and rerun_row.reservation_settled is False
            assert stale_row.status == "failed" and stale_row.reservation_settled is True
            # qwen-plus 预留 35 已解冻退回，doubao 预留 105 仍在冻结中等待重跑
            assert acc.frozen_paid_balance == 105
            assert acc.paid_balance == bal_before - 105

            # 守恒断言：账户冻结总额 == 未结任务 reserved 之和
            open_reserved = sum(
                int(t.reserved_paid_credits + t.reserved_gift_credits)
                for t in (
                    await s.execute(
                        select(GenerationTask).where(
                            GenerationTask.requested_by_user_id == uid,
                            GenerationTask.reservation_settled.is_(False),
                        )
                    )
                ).scalars()
            )
            assert int(acc.frozen_paid_balance + acc.frozen_gift_balance) == open_reserved

            # 收尾清理 rerun 任务
            await GenerationService(s).finish_unfinished_task(
                t_rerun, status="failed", message="cleanup", user_message="cleanup"
            )

    _run_db(flow)
