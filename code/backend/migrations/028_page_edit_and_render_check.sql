-- 续写局部编辑与发布前渲染检查：
-- 1) generation_tasks.model_edit_text 记录模型原始输出的 SEARCH/REPLACE 编辑块（排查用；model_output_text 仍存合并后的完整 HTML）。
-- 2) page_versions.render_check 记录发布前无头浏览器渲染检查结果、问题列表、检查模式与是否自动修复过。
-- 均为可空列，支持先执行迁移再滚动重启 worker 与 web。可重复执行。

ALTER TABLE generation_tasks
  ADD COLUMN IF NOT EXISTS model_edit_text text;

ALTER TABLE page_versions
  ADD COLUMN IF NOT EXISTS render_check jsonb;
