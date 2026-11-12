-- 停止请求标记：web 进程不在本地跑生成，停止正在调用模型的任务时只写这个时间并通知 worker，
-- 由 worker 停止并写终态。worker 重启扫尾时，带这个标记的未结束任务直接写成已停止，不再重跑。新增列，可重复执行。

ALTER TABLE generation_tasks
  ADD COLUMN IF NOT EXISTS cancel_requested_at timestamptz;
