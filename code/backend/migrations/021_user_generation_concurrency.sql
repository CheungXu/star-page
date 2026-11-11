-- 每个用户同时生成的模型数上限。为空表示用配置里的默认值（GENERATION_USER_CONCURRENCY，默认 10）；
-- 以后会员按套餐放宽时，可以直接写这一列，或在代码里按套餐计算。新增列，可重复执行。

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS generation_concurrency integer;

ALTER TABLE users DROP CONSTRAINT IF EXISTS chk_users_generation_concurrency;
ALTER TABLE users
  ADD CONSTRAINT chk_users_generation_concurrency CHECK (generation_concurrency IS NULL OR generation_concurrency > 0);
