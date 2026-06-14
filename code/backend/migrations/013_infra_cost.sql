-- 基础设施成本科目：阿里云账单里的服务器/存储/带宽等（非百炼 LLM）部分计入此科目。
-- 百炼 LLM 已按次计入 6001 算力成本，避免重复计算；这里只记基础设施作为期间费用。
INSERT INTO ledger_accounts(code, name, type) VALUES
  ('6002', '基础设施成本（服务器等）', 'expense')
ON CONFLICT (code) DO NOTHING;
