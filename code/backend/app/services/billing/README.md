# billing 计费服务

积分计费、TCC 动态预授权冻结与复式记账的核心实现。设计见 `doc/20260614/billing-system-plan.md` 与 `doc/20261112/billing-unit-economics-and-preauth-plan.md`，通用经验见 `wiki/credit-billing-and-double-entry-accounting.md`。

- `pricing.py`：定价与安全地板。`credits_for_cost(原始成本, 倍率) = max(ceil(成本×倍率×100), 1)`；`apply_training_discount` 支持向下取整 + `cost_floor_credits`（默认 `1.25x` 原始成本）托底，杜绝小额任务负毛利；`discounted_hold_credits` 计算授权状态预留额。
- `account.py`：`BillingService` 钱包与编排——
  - `grant_signup_bonus`：新注册赠送 `450` 积分 + `1` 次旗舰双模首秀券（`free_flagship_batches_remaining`），幂等；
  - `reserve_for_batch`（Try）：提交生成时加 `SELECT ... FOR UPDATE` 行锁，校验 `debt_credits == 0`、首秀券/旗舰权限与可用余额，按模型 P90 预留额从 `gift/paid` 移入 `frozen_gift/frozen_paid`；
  - `settle_generation`（Confirm）：按 `version_id` 幂等结算，先解回本任务预留再按实收扣费（多余自动退回可用桶）；超长页超出预留且可用不足时记入 `debt_credits`（单任务封顶 `100` 积分）；支持向用户按成功轮收费、总账 `6001` 按含重试真实总成本入账；
  - `release_task_reservation` / `settle_cancelled_task`（Cancel）：报错失败或输出未满 `2000` Token 前取消全额解冻退回（首秀券整批全败自动退券）；满 `2000` Token 主动取消按 `1.0x` 原始成本保底扣费；
  - `apply_recharge`：充值入账（`base→paid`、`bonus→gift`），防多笔首充单重复领赠币，并自动调用 `_settle_debt_from_available` 冲抵欠费（写 `debt_settle` 流水与递延收入凭证）。
- `ledger.py`：`LedgerService.post` 复式过账，借贷必平、按 `(event_type, event_ref)` 幂等。
- `errors.py`：`BillingError` 及结构化 `InsufficientCreditsError` / `FlagshipUpgradeRequiredError` / `AnonLimitError` / `ModelNotAllowedError`，由 API 层映射 402。

幂等键约定：充值 `recharge:{order_id}`、消费 `consume:{version_id}`、取消结算 `consume:cancel:{task_id}`、赠送 `gift:signup:{user_id}`、欠费冲抵 `debt_settle:{source}`。

分层倍率、预冻结基准与围栏参数来自 `config/billing.json`（经 `app.core.config.get_billing_config()` 加载）。

附属调用单独计价：技能路由固定走廉价、关闭思考的模型，费用按该模型单价计入，不并进用户所选旗舰；长文档压缩的 usage 必须返回，按批次成功任务分摊进 `6001`，全败时单独记一笔。格式重试对用户只收成功轮，`6001` 记两轮之和。
