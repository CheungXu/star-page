from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

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


def credits_to_cny(credits: int) -> Decimal:
    """积分换算为人民币金额（元），用于记账分录。"""
    return (Decimal(int(credits)) / CREDITS_PER_CNY).quantize(Decimal("0.01"))
