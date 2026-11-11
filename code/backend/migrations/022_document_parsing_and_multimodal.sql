-- 上传资料改为后台解析，文档配图入库，整页快照只给模型看。全部为新增列，可重复执行。

-- 批次：待解析原件、解析状态、私有整页快照、每个文件在合并原文里的位置（多轮按文件去重用）。
ALTER TABLE generation_batches ADD COLUMN IF NOT EXISTS staged_uploads jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE generation_batches ADD COLUMN IF NOT EXISTS parse_status varchar(16) NOT NULL DEFAULT 'none';
ALTER TABLE generation_batches ADD COLUMN IF NOT EXISTS parse_error text;
ALTER TABLE generation_batches ADD COLUMN IF NOT EXISTS visual_snapshots jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE generation_batches ADD COLUMN IF NOT EXISTS parsed_documents jsonb NOT NULL DEFAULT '[]'::jsonb;
-- 整批都失败在资料阶段时退回一次匿名免费次数，用这一列保证只退一次。
ALTER TABLE generation_batches ADD COLUMN IF NOT EXISTS free_trial_refunded boolean NOT NULL DEFAULT false;

-- 任务：本轮从资料里提取出的内容。空串表示本轮需求和资料无关。
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS extracted_context text;

-- 托管图片：来源（场景槽位 / 用户上传 / 文档配图）、尺寸、在原文档中的位置。
ALTER TABLE page_assets ADD COLUMN IF NOT EXISTS source_kind varchar(24) NOT NULL DEFAULT 'user_upload';
ALTER TABLE page_assets ADD COLUMN IF NOT EXISTS width integer;
ALTER TABLE page_assets ADD COLUMN IF NOT EXISTS height integer;
ALTER TABLE page_assets ADD COLUMN IF NOT EXISTS source_label varchar(255);
ALTER TABLE page_assets ADD COLUMN IF NOT EXISTS sort_order integer NOT NULL DEFAULT 0;
ALTER TABLE page_assets ADD COLUMN IF NOT EXISTS content_hash varchar(64);

-- 017 之前上传的场景图都来自槽位。
UPDATE page_assets SET source_kind = 'scene_slot' WHERE source_kind = 'user_upload' AND role <> 'image';
