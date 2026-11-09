-- 会话分叉：从更早一轮的结果继续时，新分支单独成一条会话（origin='fork'），
-- 靠分叉指针接回原会话；节点的 parent_page_id 可跨会话，整棵生成树不变。
-- 全部为新增列/放宽约束，可重复执行。

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS forked_from_conversation_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS forked_from_batch_id uuid REFERENCES generation_batches(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS fork_base_page_id uuid REFERENCES pages(id) ON DELETE SET NULL;

ALTER TABLE conversations DROP CONSTRAINT IF EXISTS chk_conversations_origin;
ALTER TABLE conversations
  ADD CONSTRAINT chk_conversations_origin CHECK (origin IN ('new', 'merge', 'fork'));

CREATE INDEX IF NOT EXISTS idx_conversations_forked_from ON conversations(forked_from_conversation_id);
