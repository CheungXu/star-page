-- 网页制作技能（page-skills）：记录每轮批次/每个页面节点/每个生成任务选用的技能 key。
-- 用于续写时沿 parent 链路延用同一技能、以及前端展示"已应用技能"。
ALTER TABLE generation_batches ADD COLUMN IF NOT EXISTS skill_key varchar(64);
ALTER TABLE pages ADD COLUMN IF NOT EXISTS skill_key varchar(64);
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS skill_key varchar(64);
