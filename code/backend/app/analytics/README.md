# analytics 运营数据服务

运营后台的数据内核：明细埋点采集 + 离线聚合 + 管理端读接口。方案见 `doc/20260628/ops-backend-design-and-implementation.md`，通用方法论见 `wiki/product-ops-metrics-and-north-star.md`。

## 两层数据架构

- **明细层**（写多读少，实时口径）：`page_view_events` 生成页访问日志、`analytics_events` 前端漏斗事件。建表见 `migrations/016_analytics.sql`，ORM 见 `app/models/entities.py`。
- **聚合层**（预计算快照，趋势口径）：`metric_daily`（逐日指标，`(stat_date, metric_key, dims)` 唯一）、`retention_cohort`（留存 cohort×period）、`funnel_daily`（漏斗逐步）。

实时/明细类指标直查明细与业务表（数据新鲜）；趋势/总览/留存/漏斗/分布走聚合表（读得快、稳定）。

## 模块构成

- `aggregate.py`：聚合引擎 + CLI。按自然日（`DAY_TZ`=Asia/Shanghai）幂等 `upsert` 到三张聚合表，可安全重跑与回填。
  - 关键常量：`RETENTION_PERIODS`（D1/D3/D7/D14…）、`_ACTIVE_IDS_SQL`（活跃用户口径：发起生成 ∪ 会话心跳 ∪ 埋点）。
  - 入口：`python -m app.analytics.aggregate --help`；支持 `--date / --start / --end / --backfill N` 与按日补算留存。
- `../services/analytics/tracking.py`：采集落点。
  - `hash_ip`：IP 仅存 HMAC-SHA256（隐私），密钥取 `auth_secret_key`。
  - `record_page_view`：`/p` 网关成功返回时 fire-and-forget 写 `page_view_events`，区分作者自览(`is_owner_view`)。
  - `record_analytics_event` + 内存滑窗限频：`POST /api/analytics/collect` 的实现，event 走白名单。
- `../api/routes_analytics.py`：公开埋点接口 `POST /api/analytics/collect`（允许匿名，白名单 + IP 限频，写 `analytics_events`）。
- `../api/routes_admin_analytics.py`：管理端只读接口（`require_admin` 鉴权），前缀 `/api/admin/analytics`，对应运营后台九个 Tab。
- `../schemas/analytics.py`：采集请求与所有管理端响应的 Pydantic 模型。

## 管理端接口一览（`/api/admin/analytics`）

| 路由 | 用途 | 数据源 |
| --- | --- | --- |
| `GET /realtime` | 在线/进行中/今日累计 + 实时事件流 | 明细实时 |
| `GET /overview` | 北极星 WAGP + 核心 KPI 卡（环比） | 聚合 |
| `GET /trends` | 任意指标逐日序列（白名单 `_TREND_METRICS`） | 聚合 |
| `GET /retention` | 留存矩阵（kind=login/create） | 聚合 |
| `GET /funnel` | 全链路漏斗逐步转化 | 聚合 |
| `GET /engagement` | 续写率/上传率/人均生成 + 模型/技能分布 | 聚合 + 明细 |
| `GET /quality` | 成功率/耗时分位/各模型成功率/失败原因 | 明细实时 |
| `GET /virality` | PV/UV/分享传播比 + 热门页 TOP | 明细实时 |
| `GET /users`、`/users/{id}` | 用户列表 / 单用户画像 | 业务表 |
| `GET /cases`、`/cases/{id}` | 生成 Case 列表 / 详情（prompt/输出/SSE 事件流） | 业务表 |

## 埋点字典（前端 → `analytics_events.event_name` 白名单）

- `landing_view`：落地访问（漏斗第一步）。
- `prompt_input`：本次会话首次产生输入意图（前端按会话去重一次）。
- `generate_click`：点击生成（props：`model_count` / `has_file` / `in_conversation`）。
- `login_success`：登录/注册成功。

前端实现见 `code/frontend/app/lib/analytics.ts`（`navigator.sendBeacon`，失败静默），落点在 `code/frontend/app/page.tsx`。新增事件需同时更新后端白名单。

## 定时聚合

`code/systemd/star-page-analytics.{service,timer}.example`：每小时 :05 增量补当天，每日 00:10 收口昨日并重算留存，`Persistent=true` 补漏触发。安装见 `code/systemd/README.md`。

## 关键约定与坑

- 「注册用户」口径 = `phone IS NOT NULL`（排除中间匿名用户）；匿名访客单独看 `anon_visitors`。
- 「激活」= 注册后 24h 内成功生成一次；「留存」分登录留存与创作留存两种 cohort。
- 区间 UV / distinct 创作者不可由逐日聚合相加，需回明细去重（接口里已对这类指标走明细实时算）。
- `metric_daily` 总量行 `dims = '{}'::jsonb`；带维度（模型/技能）的行单独存，读取时分别过滤。
- 聚合幂等靠 `ON CONFLICT (stat_date, metric_key, dims) DO UPDATE`，重跑只更新不重复。
