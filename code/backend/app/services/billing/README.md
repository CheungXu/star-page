# billing 计费服务

积分计费与复式记账的核心实现。设计见 `doc/20260614/billing-system-plan.md`，通用经验见 `wiki/credit-billing-and-double-entry-accounting.md`。

- `pricing.py`：定价。`credits_for_cost(原始成本, 倍率) = max(ceil(成本×倍率×100), 1)`；`credits_to_cny` 积分换算元。
- `account.py`：`BillingService` 钱包与编排——
  - `grant_signup_bonus`（注册赠送 1000 积分，幂等）、`apply_recharge`（充值入账，base→paid 桶、bonus→gift 桶）。
  - `ensure_can_start_batch` / `validate_anon_models`（生成前置校验：余额 / 免费次数 / 匿名模型白名单与数量）。
  - `settle_generation`（按 `version_id` 幂等结算：登录扣积分 gift→paid 并确认收入 + 记 COGS；匿名仅记 COGS）。
  - `account_summary` / `create_recharge_order` / `mark_order_paid` / `list_transactions` 等用户/订单接口。
- `ledger.py`：`LedgerService.post` 复式过账，借贷必平、按 `(event_type, event_ref)` 幂等。
- `errors.py`：`BillingError` 及 `InsufficientCreditsError` / `AnonLimitError` / `ModelNotAllowedError`，由 API 层映射 402/403。

幂等键约定：充值 `recharge:{order_id}`、消费 `consume:{version_id}`、赠送 `gift:signup:{user_id}`。

倍率与匿名围栏参数来自 `config/billing.json`（经 `app.core.config.get_billing_config()` 加载）。
