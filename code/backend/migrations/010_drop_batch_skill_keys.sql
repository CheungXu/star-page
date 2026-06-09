-- 移除已不再使用的手动技能选择字段（009 引入，现改为全自动路由）。
ALTER TABLE generation_batches DROP COLUMN IF EXISTS skill_keys;
