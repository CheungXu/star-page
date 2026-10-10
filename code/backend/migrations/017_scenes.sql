-- 场景定位：场景标记、托管图片、固定发布链接、场景案例。
-- 全部为新增列/新表，旧代码不读这些列也能继续运行。

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS scene_key varchar(64);
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS utm_source varchar(64);

ALTER TABLE pages ADD COLUMN IF NOT EXISTS scene_key varchar(64);
ALTER TABLE generation_batches ADD COLUMN IF NOT EXISTS scene_key varchar(64);
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS scene_key varchar(64);
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS extra_skill_keys jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_pages_scene_key ON pages(scene_key);
CREATE INDEX IF NOT EXISTS idx_generation_tasks_scene_key ON generation_tasks(scene_key);

CREATE TABLE IF NOT EXISTS page_assets (
  id uuid PRIMARY KEY,
  owner_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  batch_id uuid,
  storage_key varchar(512) NOT NULL,
  content_type varchar(64) NOT NULL,
  filename varchar(255),
  role varchar(32) NOT NULL DEFAULT 'image',
  byte_size integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_page_assets_batch ON page_assets(batch_id);

-- 一个会话一条固定发布链接。重新发布只改 page_id，slug 不变。
CREATE TABLE IF NOT EXISTS page_publications (
  id uuid PRIMARY KEY,
  owner_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  page_id uuid NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  slug varchar(64) NOT NULL UNIQUE,
  scene_key varchar(64),
  title varchar(200) NOT NULL,
  og_description text,
  og_image_key varchar(512),
  poster_image_key varchar(512),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_page_publications_conversation ON page_publications(conversation_id);

CREATE TABLE IF NOT EXISTS scene_showcases (
  id uuid PRIMARY KEY,
  scene_key varchar(64) NOT NULL,
  page_id uuid NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  title varchar(200) NOT NULL,
  summary text,
  sort_order integer NOT NULL DEFAULT 0,
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (scene_key, page_id)
);
