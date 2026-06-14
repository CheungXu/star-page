-- 管理员身份改为数据库配置：用独立表按手机号配置管理员（白名单语义，可预授权未注册手机号）。
CREATE TABLE IF NOT EXISTS admin_phones (
  phone varchar(32) PRIMARY KEY,
  note text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- 引导：把原 config/billing.json.admin_phones 里的管理员手机号迁入，保证仍可访问后台。
-- 后续增删管理员用 script/set_admin.py，或直接增删本表行。
INSERT INTO admin_phones(phone, note) VALUES
  ('15827488805', '初始管理员（自 billing.json 迁移）')
ON CONFLICT (phone) DO NOTHING;
