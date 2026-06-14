-- 预付费模型改造：LLM/云供应商为「预先充值、调用即扣」的预付费，而非月结后付费。
-- 因此把原「应付账款-LLM供应商」(负债 2101) 重分类为「预付账款-云/LLM供应商」(资产 1102)。
-- 调用消费时贷记 1102 即视为冲减预付资产；管理员录入云账户充值时借记 1102（贷现金）。

-- 1) 新增预付资产科目（幂等）
INSERT INTO ledger_accounts(code, name, type) VALUES
  ('1102', '预付账款-云/LLM供应商', 'asset')
ON CONFLICT (code) DO NOTHING;

-- 2) 历史消费凭证里贷方 2101（彼时记作应付）迁移到 1102，语义即「冲减预付资产」
UPDATE ledger_entry_lines SET account_code = '1102' WHERE account_code = '2101';

-- 3) 移除不再使用的应付科目
DELETE FROM ledger_accounts WHERE code = '2101';
