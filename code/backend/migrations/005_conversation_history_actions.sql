-- 历史记录操作：收藏筛选与列表查询索引。

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS is_favorite boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_conversations_history_list
  ON conversations(owner_user_id, deleted_at, is_favorite, updated_at DESC, created_at DESC);
