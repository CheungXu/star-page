-- 运营后台：明细埋点（生成页访问日志、前端漏斗事件）+ 定时聚合快照（指标/留存/漏斗）
-- 设计见 doc/20260628/operations-dashboard-plan.md。
-- 双层数据架构：明细层尽量派生现有表，仅新增现有表拿不到的两类埋点；聚合层由定时任务幂等写入。

-- 1. 生成页访问日志：/p 网关每次成功返回页面时 fire-and-forget 写入。
--    用于传播指标（PV/UV/分享传播比/K 因子）。隐私上仅存 IP 的 HMAC，不落明文。
CREATE TABLE IF NOT EXISTS page_view_events (
  id uuid PRIMARY KEY,
  page_id uuid NOT NULL,
  conversation_id uuid,
  owner_user_id uuid,            -- 冗余页面归属者，便于「创作者带来多少访问」聚合
  viewer_user_id uuid,           -- 访问者（登录态才有），匿名访问为空
  is_owner_view boolean NOT NULL DEFAULT false,  -- 是否作者本人查看（剔除自访噪声）
  ip_hash varchar(64),           -- HMAC(ip)，用于 UV 估算，不可逆
  referer text,
  user_agent text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_page_view_events_created
  ON page_view_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_page_view_events_page_created
  ON page_view_events(page_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_page_view_events_owner_created
  ON page_view_events(owner_user_id, created_at DESC);

-- 2. 前端通用/漏斗事件：经 POST /api/analytics/collect 上报（允许匿名）。
--    event_name 为白名单内的枚举（落地访问、输入、点击生成、注册引导等）。
CREATE TABLE IF NOT EXISTS analytics_events (
  id uuid PRIMARY KEY,
  event_name varchar(64) NOT NULL,
  user_id uuid,                  -- 登录用户（可空）
  anon_device_id varchar(64),    -- 匿名设备（可空）
  client_session_id varchar(64), -- 前端会话 id，用于漏斗内同一访问串联
  props jsonb NOT NULL DEFAULT '{}'::jsonb,
  ip_hash varchar(64),
  referer text,
  user_agent text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analytics_events_name_created
  ON analytics_events(event_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_events_created
  ON analytics_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_events_session
  ON analytics_events(client_session_id)
  WHERE client_session_id IS NOT NULL;

-- 3. 指标日快照（KV 宽表）：date + metric_key + dims 唯一，按 date 幂等 upsert。
--    dims 用于细分维度（如按模型/技能），无维度时为 '{}'。jsonb 归一化后可参与唯一约束。
CREATE TABLE IF NOT EXISTS metric_daily (
  id uuid PRIMARY KEY,
  stat_date date NOT NULL,
  metric_key varchar(64) NOT NULL,
  dims jsonb NOT NULL DEFAULT '{}'::jsonb,
  value numeric(20, 4) NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_metric_daily UNIQUE (stat_date, metric_key, dims)
);

CREATE INDEX IF NOT EXISTS idx_metric_daily_key_date
  ON metric_daily(metric_key, stat_date DESC);

-- 4. 留存 cohort 快照：按 cohort 起始日 + 口径 + 周期偏移记录留存。
--    cohort_kind：'login'=按登录活跃留存；'create'=按「再次发起生成」的真实复用留存。
--    period_index：天偏移（1=次日，7=7 日，30=30 日，也可记任意 N）。
CREATE TABLE IF NOT EXISTS retention_cohort (
  id uuid PRIMARY KEY,
  cohort_date date NOT NULL,
  cohort_kind varchar(16) NOT NULL DEFAULT 'login',
  period_index integer NOT NULL,
  cohort_size integer NOT NULL DEFAULT 0,
  retained_count integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_retention_cohort UNIQUE (cohort_date, cohort_kind, period_index),
  CONSTRAINT chk_retention_cohort_kind CHECK (cohort_kind IN ('login', 'create'))
);

CREATE INDEX IF NOT EXISTS idx_retention_cohort_kind_date
  ON retention_cohort(cohort_kind, cohort_date DESC);

-- 5. 漏斗日快照：每个步骤一行，按 (date, step) 幂等。
--    step_order 决定漏斗展示顺序：访问→输入→点击生成→生成成功→注册→首充。
CREATE TABLE IF NOT EXISTS funnel_daily (
  id uuid PRIMARY KEY,
  stat_date date NOT NULL,
  step varchar(32) NOT NULL,
  step_order integer NOT NULL DEFAULT 0,
  count integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_funnel_daily UNIQUE (stat_date, step)
);

CREATE INDEX IF NOT EXISTS idx_funnel_daily_date
  ON funnel_daily(stat_date DESC);
