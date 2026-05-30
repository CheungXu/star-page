-- 多模型并行生成（Scheme A：会话=生成树，Node=Page，合并另起新会话）
-- 新增 conversations / generation_batches，并把 pages 升级为"节点"、generation_tasks 升级为"每节点的模型 run"。

CREATE TABLE IF NOT EXISTS conversations (
  id uuid PRIMARY KEY,
  owner_user_id uuid NOT NULL REFERENCES users(id),
  title varchar(200) NOT NULL,
  origin varchar(16) NOT NULL DEFAULT 'new',
  root_batch_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  CONSTRAINT chk_conversations_origin CHECK (origin IN ('new', 'merge'))
);

CREATE TABLE IF NOT EXISTS generation_batches (
  id uuid PRIMARY KEY,
  conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  base_page_id uuid REFERENCES pages(id) ON DELETE SET NULL,
  kind varchar(16) NOT NULL DEFAULT 'create',
  source_page_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  selected_models jsonb NOT NULL DEFAULT '[]'::jsonb,
  prompt text NOT NULL,
  user_prompt text,
  input_file_names jsonb NOT NULL DEFAULT '[]'::jsonb,
  extracted_file_text text,
  compression_prompt text,
  status varchar(16) NOT NULL DEFAULT 'pending',
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz,
  CONSTRAINT chk_generation_batches_kind CHECK (kind IN ('create', 'continue', 'merge_seed')),
  CONSTRAINT chk_generation_batches_status CHECK (status IN ('pending', 'running', 'succeeded', 'partial', 'failed', 'cancelled'))
);

-- conversations.root_batch_id -> generation_batches（便利指针，批次表建好后补 FK）
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_conversations_root_batch_id'
  ) THEN
    ALTER TABLE conversations
      ADD CONSTRAINT fk_conversations_root_batch_id
      FOREIGN KEY (root_batch_id) REFERENCES generation_batches(id);
  END IF;
END $$;

-- pages 升级为"节点"
ALTER TABLE pages
  ADD COLUMN IF NOT EXISTS conversation_id uuid,
  ADD COLUMN IF NOT EXISTS batch_id uuid,
  ADD COLUMN IF NOT EXISTS parent_page_id uuid,
  ADD COLUMN IF NOT EXISTS model_key varchar(64),
  ADD COLUMN IF NOT EXISTS model_provider varchar(64),
  ADD COLUMN IF NOT EXISTS model_name varchar(128);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_pages_conversation_id') THEN
    ALTER TABLE pages ADD CONSTRAINT fk_pages_conversation_id
      FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_pages_batch_id') THEN
    ALTER TABLE pages ADD CONSTRAINT fk_pages_batch_id
      FOREIGN KEY (batch_id) REFERENCES generation_batches(id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_pages_parent_page_id') THEN
    ALTER TABLE pages ADD CONSTRAINT fk_pages_parent_page_id
      FOREIGN KEY (parent_page_id) REFERENCES pages(id) ON DELETE SET NULL;
  END IF;
END $$;

-- generation_tasks 升级为"每节点的模型 run"
ALTER TABLE generation_tasks
  ADD COLUMN IF NOT EXISTS batch_id uuid,
  ADD COLUMN IF NOT EXISTS model_key varchar(64),
  ADD COLUMN IF NOT EXISTS model_provider varchar(64),
  ADD COLUMN IF NOT EXISTS model_name varchar(128);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_generation_tasks_batch_id') THEN
    ALTER TABLE generation_tasks ADD CONSTRAINT fk_generation_tasks_batch_id
      FOREIGN KEY (batch_id) REFERENCES generation_batches(id) ON DELETE CASCADE;
  END IF;
END $$;

-- page_versions 按模型分组与标识
ALTER TABLE page_versions
  ADD COLUMN IF NOT EXISTS batch_id uuid,
  ADD COLUMN IF NOT EXISTS model_key varchar(64);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_page_versions_batch_id') THEN
    ALTER TABLE page_versions ADD CONSTRAINT fk_page_versions_batch_id
      FOREIGN KEY (batch_id) REFERENCES generation_batches(id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_generation_batches_conversation ON generation_batches(conversation_id);
CREATE INDEX IF NOT EXISTS idx_pages_conversation ON pages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_pages_batch ON pages(batch_id);
CREATE INDEX IF NOT EXISTS idx_generation_tasks_batch ON generation_tasks(batch_id);
CREATE INDEX IF NOT EXISTS idx_page_versions_batch ON page_versions(batch_id);
