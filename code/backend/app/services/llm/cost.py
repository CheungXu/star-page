from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.core.config import get_model_registry
from app.services.llm.types import LlmCostBreakdown, LlmUsage

_MILLION = Decimal("1000000")
_COST_QUANT = Decimal("0.00000001")


def estimate_llm_cost(model_key: str, usage: LlmUsage | None) -> LlmCostBreakdown | None:
    if usage is None or usage.input_tokens is None or usage.output_tokens is None:
        return None

    model = get_model_registry().get(model_key)
    if model is None or not model.pricing:
        return None

    tiers = model.pricing.get("tiers") or []
    if not tiers:
        return None

    input_tokens = max(0, int(usage.input_tokens))
    output_tokens = max(0, int(usage.output_tokens))
    cached_input_tokens = max(0, int(usage.cached_input_tokens or 0))
    reasoning_tokens = max(0, int(usage.reasoning_tokens or 0))
    cached_input_tokens = min(cached_input_tokens, input_tokens)

    tier = _pick_tier(tiers, input_tokens)
    input_rate = Decimal(str(tier.get("input_per_million", 0)))
    output_rate = Decimal(str(tier.get("output_per_million", 0)))
    cache_rate_raw = tier.get("cache_input_per_million")
    cache_rate = Decimal(str(cache_rate_raw)) if cache_rate_raw is not None else input_rate

    billable_input_tokens = input_tokens - cached_input_tokens
    input_cost = (
        Decimal(billable_input_tokens) * input_rate + Decimal(cached_input_tokens) * cache_rate
    ) / _MILLION
    output_cost = Decimal(output_tokens) * output_rate / _MILLION
    total_cost = input_cost + output_cost

    return LlmCostBreakdown(
        currency=str(model.pricing.get("currency") or "CNY"),
        tier_label=str(tier.get("label") or "默认"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        input_per_million=float(input_rate),
        output_per_million=float(output_rate),
        cache_input_per_million=float(cache_rate) if cache_rate_raw is not None else None,
        input_cost_cny=_to_float(input_cost),
        output_cost_cny=_to_float(output_cost),
        total_cost_cny=_to_float(total_cost),
    )


def usage_to_payload(usage: LlmUsage) -> dict[str, int]:
    payload: dict[str, int] = {}
    if usage.input_tokens is not None:
        payload["input_tokens"] = usage.input_tokens
    if usage.output_tokens is not None:
        payload["output_tokens"] = usage.output_tokens
    if usage.total_tokens is not None:
        payload["total_tokens"] = usage.total_tokens
    if usage.cached_input_tokens is not None:
        payload["cached_input_tokens"] = usage.cached_input_tokens
    if usage.reasoning_tokens is not None:
        payload["reasoning_tokens"] = usage.reasoning_tokens
    return payload


def cost_to_payload(cost: LlmCostBreakdown) -> dict[str, float | str | None]:
    return {
        "currency": cost.currency,
        "tier_label": cost.tier_label,
        "input": cost.input_cost_cny,
        "output": cost.output_cost_cny,
        "total": cost.total_cost_cny,
        "input_per_million": cost.input_per_million,
        "output_per_million": cost.output_per_million,
        "cache_input_per_million": cost.cache_input_per_million,
    }


def _pick_tier(tiers: list[dict], input_tokens: int) -> dict:
    sorted_tiers = sorted(
        tiers,
        key=lambda tier: tier.get("max_input_tokens")
        if tier.get("max_input_tokens") is not None
        else 10**18,
    )
    for tier in sorted_tiers:
        max_input = tier.get("max_input_tokens")
        if max_input is None or input_tokens <= int(max_input):
            return tier
    return sorted_tiers[-1]


def _to_float(value: Decimal) -> float:
    return float(value.quantize(_COST_QUANT, rounding=ROUND_HALF_UP))
