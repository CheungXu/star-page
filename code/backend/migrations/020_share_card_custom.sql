-- 分享卡片自定义：用户可以改写分享图 / 链接卡片的标题、副标题和文字对齐。
-- custom_* 为空表示用默认（标题取生成页 <title>，副标题取场景标语或「由星页 StarPage 生成」）；
-- title / og_description 仍存生效值，供 /u/{slug} 的 meta 与分享图直接使用。全部为新增列，可重复执行。

ALTER TABLE page_publications
  ADD COLUMN IF NOT EXISTS custom_title varchar(200),
  ADD COLUMN IF NOT EXISTS custom_subtitle varchar(300),
  ADD COLUMN IF NOT EXISTS card_align varchar(16) NOT NULL DEFAULT 'center';

ALTER TABLE page_publications DROP CONSTRAINT IF EXISTS chk_page_publications_card_align;
ALTER TABLE page_publications
  ADD CONSTRAINT chk_page_publications_card_align CHECK (card_align IN ('left', 'center', 'right'));
