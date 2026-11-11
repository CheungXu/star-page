-- 023: 计费单位经济调优、P90 预授权冻结桶、小额欠费兜底与旗舰首秀券
-- 全部采用幂等 DDL，支持安全重复执行。

-- 1. credit_accounts：新增冻结双桶、小额欠费桶、未充值旗舰首秀剩余轮次与发放标记
ALTER TABLE credit_accounts
  ADD COLUMN IF NOT EXISTS frozen_paid_balance bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS frozen_gift_balance bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS debt_credits bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS free_flagship_batches_remaining integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS flagship_trial_granted boolean NOT NULL DEFAULT false;

-- 只给未充值的已注册用户补 1 次首秀券（重复执行无副作用：已发过的不再补回）
UPDATE credit_accounts ca
SET free_flagship_batches_remaining = 1,
    flagship_trial_granted = true
FROM users u
WHERE u.id = ca.user_id
  AND u.is_anonymous = false
  AND ca.total_recharged_credits = 0
  AND ca.flagship_trial_granted = false;

ALTER TABLE credit_accounts DROP CONSTRAINT IF EXISTS chk_credit_accounts_frozen_paid;
ALTER TABLE credit_accounts DROP CONSTRAINT IF EXISTS chk_credit_accounts_frozen_gift;
ALTER TABLE credit_accounts DROP CONSTRAINT IF EXISTS chk_credit_accounts_debt;
ALTER TABLE credit_accounts DROP CONSTRAINT IF EXISTS chk_credit_accounts_flagship_trial;

ALTER TABLE credit_accounts
  ADD CONSTRAINT chk_credit_accounts_frozen_paid CHECK (frozen_paid_balance >= 0),
  ADD CONSTRAINT chk_credit_accounts_frozen_gift CHECK (frozen_gift_balance >= 0),
  ADD CONSTRAINT chk_credit_accounts_debt CHECK (debt_credits >= 0),
  ADD CONSTRAINT chk_credit_accounts_flagship_trial CHECK (free_flagship_batches_remaining >= 0);

-- 2. generation_batches：标记本批次是否消耗了未充值旗舰首秀券及全败退券幂等位
ALTER TABLE generation_batches
  ADD COLUMN IF NOT EXISTS used_flagship_trial boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS flagship_trial_refunded boolean NOT NULL DEFAULT false;

-- 3. generation_tasks：记录单任务预授权冻结明细与结算释放状态（保证释放严格幂等）
ALTER TABLE generation_tasks
  ADD COLUMN IF NOT EXISTS reserved_paid_credits bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS reserved_gift_credits bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS reservation_settled boolean NOT NULL DEFAULT false;

-- 4. 积分流水类型增加欠费冲抵 debt_settle
ALTER TABLE credit_transactions DROP CONSTRAINT IF EXISTS chk_credit_transactions_type;
ALTER TABLE credit_transactions
  ADD CONSTRAINT chk_credit_transactions_type
  CHECK (type IN ('recharge', 'gift', 'consume', 'refund', 'adjust', 'expire', 'debt_settle'));

-- 5. credit_packages：上线首充包与轻量包，更新阶梯赠币（保留历史订单引用，旧 starter_10 下架）
UPDATE credit_packages SET is_active = false, updated_at = now() WHERE key = 'starter_10';

INSERT INTO credit_packages (key, title, amount_cny, base_credits, bonus_credits, is_active, sort_order)
VALUES
  ('first_19_9',   '首充体验包',  19.90,  1990,  100, true, 1),
  ('starter_20',   '轻量包',      20.00,  2000,    0, true, 2),
  ('basic_50',     '基础包',      50.00,  5000,  300, true, 3),
  ('pro_100',      '专业包',     100.00, 10000, 1000, true, 4),
  ('flagship_500', '旗舰包',     500.00, 50000, 7500, true, 5)
ON CONFLICT (key) DO UPDATE SET
  title = EXCLUDED.title,
  amount_cny = EXCLUDED.amount_cny,
  base_credits = EXCLUDED.base_credits,
  bonus_credits = EXCLUDED.bonus_credits,
  is_active = EXCLUDED.is_active,
  sort_order = EXCLUDED.sort_order,
  updated_at = now();
