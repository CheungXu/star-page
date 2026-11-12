-- 批次文档解析尝试次数：防止触发崩溃或 OOM 的「毒文件」在多个 worker 之间无限接手重做。
-- 老代码不读该列，默认值为 0，支持先执行迁移再滚动重启 worker。可重复执行。

ALTER TABLE generation_batches
  ADD COLUMN IF NOT EXISTS parse_attempts integer NOT NULL DEFAULT 0;
