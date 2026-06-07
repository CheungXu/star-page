-- 页面版本记录 LLM 用量明细与费用（按官方单价自算，接口不返回 cost 字段）

ALTER TABLE page_versions
  ADD COLUMN IF NOT EXISTS cached_input_tokens integer,
  ADD COLUMN IF NOT EXISTS reasoning_tokens integer,
  ADD COLUMN IF NOT EXISTS input_cost_cny numeric(18, 8),
  ADD COLUMN IF NOT EXISTS output_cost_cny numeric(18, 8),
  ADD COLUMN IF NOT EXISTS total_cost_cny numeric(18, 8);
