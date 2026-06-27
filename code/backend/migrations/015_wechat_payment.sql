-- 微信 Native 支付接入：充值订单补商户订单号、新增支付手续费科目
-- 设计见 doc/20260617/wechat-native-pay-integration-plan.md。

-- 1. 充值订单补商户订单号 out_trade_no（= 订单 UUID 的 hex，便于查单与后台检索）
ALTER TABLE recharge_orders
  ADD COLUMN IF NOT EXISTS out_trade_no varchar(32);

CREATE UNIQUE INDEX IF NOT EXISTS idx_recharge_orders_out_trade_no_unique
  ON recharge_orders(out_trade_no)
  WHERE out_trade_no IS NOT NULL;

-- 历史订单回填（UUID 去连字符即 hex），幂等。
UPDATE recharge_orders
  SET out_trade_no = replace(id::text, '-', '')
  WHERE out_trade_no IS NULL;

-- 2. 支付手续费科目（微信结算时按账单扣除的渠道手续费）
INSERT INTO ledger_accounts(code, name, type) VALUES
  ('6603', '支付手续费', 'expense')
ON CONFLICT (code) DO NOTHING;
