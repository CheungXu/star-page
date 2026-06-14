-- 计费系统：积分钱包、流水、充值订单、套餐、复式记账总账，以及匿名访客与归并字段
-- 设计见 doc/20260614/billing-system-plan.md；积分整数存储，1 元 = 100 积分。

-- 1. users 增补匿名与归并字段
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS is_anonymous boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS anon_device_id varchar(64),
  ADD COLUMN IF NOT EXISTS merged_into_user_id uuid REFERENCES users(id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_anon_device_id_unique
  ON users(anon_device_id)
  WHERE anon_device_id IS NOT NULL;

-- 2. 匿名访客（支撑 IP 天花板与风控审计）
CREATE TABLE IF NOT EXISTS anon_visitors (
  id uuid PRIMARY KEY,
  anon_device_id varchar(64) NOT NULL UNIQUE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  sign_ip varchar(64),
  user_agent text,
  free_generations_used integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_anon_visitors_free_used CHECK (free_generations_used >= 0)
);

CREATE INDEX IF NOT EXISTS idx_anon_visitors_sign_ip_created
  ON anon_visitors(sign_ip, created_at DESC);

-- 3. 积分钱包（每用户一行；积分整数；区分充值/赠送两个桶）
CREATE TABLE IF NOT EXISTS credit_accounts (
  user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  paid_balance bigint NOT NULL DEFAULT 0,
  gift_balance bigint NOT NULL DEFAULT 0,
  free_generations_used integer NOT NULL DEFAULT 0,
  total_recharged_credits bigint NOT NULL DEFAULT 0,
  total_spent_credits bigint NOT NULL DEFAULT 0,
  signup_bonus_granted boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_credit_accounts_paid_balance CHECK (paid_balance >= 0),
  CONSTRAINT chk_credit_accounts_gift_balance CHECK (gift_balance >= 0)
);

-- 4. 积分流水（追加写、幂等）
CREATE TABLE IF NOT EXISTS credit_transactions (
  id uuid PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type varchar(16) NOT NULL,
  credits_delta bigint NOT NULL,
  paid_delta bigint NOT NULL DEFAULT 0,
  gift_delta bigint NOT NULL DEFAULT 0,
  balance_after bigint NOT NULL DEFAULT 0,
  ref_type varchar(32),
  ref_id varchar(64),
  model_key varchar(64),
  raw_cost_cny numeric(18, 8),
  markup numeric(10, 4),
  revenue_cny numeric(18, 8),
  idempotency_key varchar(128) NOT NULL UNIQUE,
  memo text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_credit_transactions_type
    CHECK (type IN ('recharge', 'gift', 'consume', 'refund', 'adjust', 'expire'))
);

CREATE INDEX IF NOT EXISTS idx_credit_transactions_user_created
  ON credit_transactions(user_id, created_at DESC);

-- 5. 充值订单
CREATE TABLE IF NOT EXISTS recharge_orders (
  id uuid PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  package_key varchar(64) NOT NULL,
  amount_cny numeric(18, 2) NOT NULL,
  base_credits bigint NOT NULL,
  bonus_credits bigint NOT NULL DEFAULT 0,
  status varchar(16) NOT NULL DEFAULT 'pending',
  payment_provider varchar(16) NOT NULL DEFAULT 'mock',
  provider_txn_id varchar(128),
  created_at timestamptz NOT NULL DEFAULT now(),
  paid_at timestamptz,
  CONSTRAINT chk_recharge_orders_status
    CHECK (status IN ('pending', 'paid', 'failed', 'refunded')),
  CONSTRAINT chk_recharge_orders_provider
    CHECK (payment_provider IN ('mock', 'wechat', 'alipay'))
);

CREATE INDEX IF NOT EXISTS idx_recharge_orders_user_created
  ON recharge_orders(user_id, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_recharge_orders_provider_txn_unique
  ON recharge_orders(payment_provider, provider_txn_id)
  WHERE provider_txn_id IS NOT NULL;

-- 6. 积分套餐（mock，可后续后台维护）
CREATE TABLE IF NOT EXISTS credit_packages (
  key varchar(64) PRIMARY KEY,
  title varchar(120) NOT NULL,
  amount_cny numeric(18, 2) NOT NULL,
  base_credits bigint NOT NULL,
  bonus_credits bigint NOT NULL DEFAULT 0,
  is_active boolean NOT NULL DEFAULT true,
  sort_order integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- 7. 复式记账：科目表 / 凭证 / 分录行
CREATE TABLE IF NOT EXISTS ledger_accounts (
  code varchar(16) PRIMARY KEY,
  name varchar(120) NOT NULL,
  type varchar(16) NOT NULL,
  CONSTRAINT chk_ledger_accounts_type
    CHECK (type IN ('asset', 'liability', 'equity', 'revenue', 'expense'))
);

CREATE TABLE IF NOT EXISTS ledger_entries (
  id uuid PRIMARY KEY,
  event_type varchar(32) NOT NULL,
  event_ref varchar(128),
  user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  memo text,
  posted_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_ledger_entries_event UNIQUE (event_type, event_ref)
);

CREATE INDEX IF NOT EXISTS idx_ledger_entries_posted ON ledger_entries(posted_at DESC);

CREATE TABLE IF NOT EXISTS ledger_entry_lines (
  id uuid PRIMARY KEY,
  entry_id uuid NOT NULL REFERENCES ledger_entries(id) ON DELETE CASCADE,
  account_code varchar(16) NOT NULL REFERENCES ledger_accounts(code),
  debit numeric(18, 8) NOT NULL DEFAULT 0,
  credit numeric(18, 8) NOT NULL DEFAULT 0,
  CONSTRAINT chk_ledger_entry_lines_nonneg CHECK (debit >= 0 AND credit >= 0)
);

CREATE INDEX IF NOT EXISTS idx_ledger_entry_lines_entry ON ledger_entry_lines(entry_id);
CREATE INDEX IF NOT EXISTS idx_ledger_entry_lines_account ON ledger_entry_lines(account_code);

-- 8. 科目种子（幂等）
INSERT INTO ledger_accounts(code, name, type) VALUES
  ('1001', '现金/在途', 'asset'),
  ('1002', '应收第三方支付', 'asset'),
  ('2001', '预收账款-充值积分', 'liability'),
  ('2002', '赠送积分负债', 'liability'),
  ('2101', '应付账款-LLM供应商', 'liability'),
  ('5001', '服务收入', 'revenue'),
  ('6001', 'LLM算力成本', 'expense'),
  ('6601', '推广赠送费用', 'expense')
ON CONFLICT (code) DO NOTHING;

-- 9. 套餐种子（mock，越多越优惠）
INSERT INTO credit_packages(key, title, amount_cny, base_credits, bonus_credits, sort_order) VALUES
  ('starter_10',  '入门包',   10.00,  1000,    0, 1),
  ('basic_50',    '基础包',   50.00,  5000,  500, 2),
  ('pro_100',     '专业包',  100.00, 10000, 1500, 3),
  ('flagship_500','旗舰包',  500.00, 50000, 12500, 4)
ON CONFLICT (key) DO NOTHING;
