"""积分定价纯逻辑单测：ceil 向上取整、模型倍率、最小 1 积分兜底。

无需额外依赖，可直接运行：
    .venv/bin/python tests/test_pricing.py
也兼容 pytest。
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.billing.pricing import credits_for_cost, credits_to_cny  # noqa: E402


def test_basic_ceil():
    # 0.05 × 1.2 × 100 = 6.0 → 6
    assert credits_for_cost(0.05, 1.2) == 6
    # 0.0501 × 1.2 × 100 = 6.012 → 向上取整 7
    assert credits_for_cost(0.0501, 1.2) == 7


def test_min_one_floor():
    # 极小成本取整后不足 1，兜底为 1，避免白嫖
    assert credits_for_cost(0.0001, 1.2) == 1
    assert credits_for_cost(0, 1.2) == 1
    assert credits_for_cost(None, 1.2) == 1


def test_discount_markup():
    # 倍率 < 1（打折）：0.1 × 0.5 × 100 = 5
    assert credits_for_cost(0.1, 0.5) == 5


def test_decimal_input():
    assert credits_for_cost(Decimal("0.123456"), 1.2) == 15  # 0.123456*1.2*100=14.81472 → 15


def test_credits_to_cny():
    assert credits_to_cny(100) == Decimal("1.00")
    assert credits_to_cny(1000) == Decimal("10.00")
    assert credits_to_cny(0) == Decimal("0.00")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    if failures:
        print(f"\n{failures} 个用例失败")
        sys.exit(1)
    print("\n全部用例通过")
