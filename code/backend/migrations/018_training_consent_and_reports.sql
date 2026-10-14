-- 训练授权、授权折扣、匿名结构样本、违规举报。
-- 全部为新增列/新表，可重复执行；旧代码不读这些列也能继续运行。

-- 用户当前授权状态。默认授权（协议约定默认开启，可在设置中关闭）。
ALTER TABLE users ADD COLUMN IF NOT EXISTS allow_training boolean NOT NULL DEFAULT true;
ALTER TABLE users ADD COLUMN IF NOT EXISTS training_updated_at timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS policy_notice_seen_at timestamptz;

-- 同意记录：只追加，不修改，作为用户同意过的证据。
-- event：login_agree 登录勾选 / anon_notice 匿名使用即同意 / notice_ack 老用户确认告知 /
--        preference 开关变更 / merge_opt_out 匿名期已关闭授权，登录后沿用
CREATE TABLE IF NOT EXISTS user_consents (
  id uuid PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  event varchar(32) NOT NULL,
  policy_version varchar(32) NOT NULL,
  allow_training boolean NOT NULL,
  ip_hash varchar(64),
  user_agent text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_consents_user ON user_consents(user_id, created_at);

-- 每条数据生成时的授权快照：granted 已授权 / denied 未授权 / legacy 协议上线前生成。
-- 先加可空列，再把存量回填为 legacy；新数据一律由代码写入，回填语句重跑时不会误改。
ALTER TABLE generation_batches ADD COLUMN IF NOT EXISTS training_consent varchar(16);
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS training_consent varchar(16);
ALTER TABLE page_versions ADD COLUMN IF NOT EXISTS training_consent varchar(16);
ALTER TABLE page_assets ADD COLUMN IF NOT EXISTS training_consent varchar(16);
ALTER TABLE analytics_events ADD COLUMN IF NOT EXISTS training_consent varchar(16);

UPDATE generation_batches SET training_consent = 'legacy' WHERE training_consent IS NULL;
UPDATE generation_tasks SET training_consent = 'legacy' WHERE training_consent IS NULL;
UPDATE page_versions SET training_consent = 'legacy' WHERE training_consent IS NULL;
UPDATE page_assets SET training_consent = 'legacy' WHERE training_consent IS NULL;
UPDATE analytics_events SET training_consent = 'legacy' WHERE training_consent IS NULL;

CREATE INDEX IF NOT EXISTS idx_page_versions_training_consent ON page_versions(training_consent);

-- 授权折扣：消费流水记下本次少扣的积分，便于对账。
ALTER TABLE credit_transactions ADD COLUMN IF NOT EXISTS training_discount_credits bigint NOT NULL DEFAULT 0;

-- 匿名结构样本：授权生成完成时当场提取，只保留结构/样式/交互代码，正文与链接全部替换。
-- 不存用户 ID、页面 ID，撤回授权不影响；content_hash 去重。
CREATE TABLE IF NOT EXISTS training_samples (
  id uuid PRIMARY KEY,
  scene_key varchar(64),
  model_key varchar(64),
  html_skeleton text NOT NULL,
  content_hash varchar(64) NOT NULL UNIQUE,
  extractor_version varchar(16) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- 页面审核状态：ok 正常 / blocked 已下架。
ALTER TABLE pages ADD COLUMN IF NOT EXISTS moderation_status varchar(16) NOT NULL DEFAULT 'ok';
ALTER TABLE pages ADD COLUMN IF NOT EXISTS moderated_at timestamptz;

-- 违规举报。status：pending 待审核 / taken_down 已下架 / rejected 驳回。
CREATE TABLE IF NOT EXISTS page_reports (
  id uuid PRIMARY KEY,
  page_id uuid NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  slug varchar(64),
  category varchar(32) NOT NULL,
  description text,
  contact varchar(128),
  reporter_user_id uuid,
  reporter_ip_hash varchar(64),
  reporter_user_agent text,
  status varchar(16) NOT NULL DEFAULT 'pending',
  reviewer_user_id uuid,
  reviewed_at timestamptz,
  review_note text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_page_reports_status ON page_reports(status, created_at);
CREATE INDEX IF NOT EXISTS idx_page_reports_page ON page_reports(page_id);
CREATE INDEX IF NOT EXISTS idx_page_reports_ip ON page_reports(reporter_ip_hash, created_at);
