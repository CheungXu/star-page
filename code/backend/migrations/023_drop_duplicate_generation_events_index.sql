-- (task_id, sequence) 已有唯一约束 uq_generation_events_task_sequence，这条普通索引是重复的。
-- 001 里对应的 CREATE INDEX 已删除：迁移每次都会重跑全部 SQL，留着会先重建再被本文件删掉。
DROP INDEX IF EXISTS idx_generation_events_task_sequence;
