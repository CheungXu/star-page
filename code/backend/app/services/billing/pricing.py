from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

# 1 元 = 100 积分
CREDITS_PER_CNY = Decimal("100")


def credits_for_cost(raw_cost_cny: Decimal | float | int | None, markup: float | Decimal) -> int:
    """按原始成本(元) × 模型倍率 折算应扣积分。

    规则：积分 = ceil(原始成本 × 倍率 × 100)，非整数一律向上取整；
    并设底线 max(结果, 1)，避免极小成本被取整为 0 导致白嫖。
    """
    if raw_cost_cny is None:
        return 1

    cost = Decimal(str(raw_cost_cny))
    if cost <= 0:
        return 1

    rate = Decimal(str(markup))
    credits = (cost * rate * CREDITS_PER_CNY).to_integral_value(rounding=ROUND_CEILING)
    return max(int(credits), 1)


def cost_floor_credits(raw_cost_cny: Decimal | float | int | None, floor_multiplier: float | Decimal) -> int:
    """成本安全地板积分：ceil(原始成本 × 地板倍率 × 100)，保底 1 积分。floor_multiplier<=0 时返回 1。"""
    if raw_cost_cny is None:
        return 1
    cost = Decimal(str(raw_cost_cny))
    mult = Decimal(str(floor_multiplier))
    if cost <= 0 or mult <= 0:
        return 1
    floored = (cost * mult * CREDITS_PER_CNY).to_integral_value(rounding=ROUND_CEILING)
    return max(int(floored), 1)


def apply_training_discount(
    credits: int,
    rate: float | Decimal,
    *,
    raw_cost_cny: Decimal | float | int | None = None,
    floor_multiplier: float | Decimal = 0,
) -> int:
    """授权折扣后的应扣积分：向下取整，同时受成本安全地板保护，绝不超过标准价且保底 1 积分。"""
    std = max(int(credits), 1)
    discounted = (Decimal(std) * Decimal(str(rate))).to_integral_value(rounding=ROUND_FLOOR)
    floor_val = cost_floor_credits(raw_cost_cny, floor_multiplier) if raw_cost_cny is not None else 1
    return min(std, max(int(discounted), floor_val, 1))


def discounted_hold_credits(hold_credits: int, rate: float | Decimal) -> int:
    """开启训练授权时的预冻结积分：按授权折扣率向上取整，保证覆盖授权结算上限。"""
    base = max(int(hold_credits), 1)
    r = Decimal(str(rate))
    if r >= 1:
        return base
    val = (Decimal(base) * r).to_integral_value(rounding=ROUND_CEILING)
    return min(base, max(int(val), 1))


def credits_to_cny(credits: int) -> Decimal:
    """积分换算为人民币金额（元），用于记账分录。"""
    return (Decimal(int(credits)) / CREDITS_PER_CNY).quantize(Decimal("0.01"))
