ALTER TABLE users
  ADD COLUMN IF NOT EXISTS phone varchar(32),
  ADD COLUMN IF NOT EXISTS phone_verified boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS password_set_at timestamptz,
  ADD COLUMN IF NOT EXISTS last_login_at timestamptz;

ALTER TABLE users
  ALTER COLUMN password_hash DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique
  ON users(phone)
  WHERE phone IS NOT NULL;

CREATE TABLE IF NOT EXISTS sms_verification_codes (
  id uuid PRIMARY KEY,
  phone varchar(32) NOT NULL,
  scene varchar(32) NOT NULL,
  code_hash varchar(128) NOT NULL,
  expires_at timestamptz NOT NULL,
  attempt_count integer NOT NULL DEFAULT 0,
  sent_ip varchar(64),
  consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_sms_verification_scene CHECK (scene IN ('login')),
  CONSTRAINT chk_sms_verification_attempt_count CHECK (attempt_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_sms_verification_phone_scene_created
  ON sms_verification_codes(phone, scene, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sms_verification_sent_ip_created
  ON sms_verification_codes(sent_ip, created_at DESC);

CREATE TABLE IF NOT EXISTS user_sessions (
  id uuid PRIMARY KEY,
  session_token_hash varchar(128) NOT NULL UNIQUE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  user_agent text,
  ip_address varchar(64),
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_expires
  ON user_sessions(user_id, expires_at DESC);
