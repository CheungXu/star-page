-- 多个生成 worker 同时在线：任务和批次解析都记下是哪个 worker 在做、最近一次续期时间。
-- 续期超时的才被别的 worker 接手，在线 worker 手上的不会被重跑。
-- 名额（个人 / 全站同时生成数）直接按 status = 'running' 的行数算，不另存计数。新增列和索引，可重复执行。

ALTER TABLE generation_tasks
  ADD COLUMN IF NOT EXISTS worker_id varchar(64),
  ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz;

ALTER TABLE generation_batches
  ADD COLUMN IF NOT EXISTS parse_worker_id varchar(64),
  ADD COLUMN IF NOT EXISTS parse_heartbeat_at timestamptz;

-- 认领时按提交顺序找排队任务、按用户数在跑任务，两类行都很少，用部分索引。
CREATE INDEX IF NOT EXISTS ix_generation_tasks_pending_order
  ON generation_tasks (created_at, id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS ix_generation_tasks_running_user
  ON generation_tasks (requested_by_user_id) WHERE status = 'running';
