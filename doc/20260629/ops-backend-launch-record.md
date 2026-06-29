# 运营后台上线记录

## 背景

运营后台（`/ops`）已完成开发与本地验证，本次按现有 systemd 常驻架构上线生产。

## 上线步骤

1. **数据库**：执行迁移 `016_analytics.sql`（`python -m app.db.migrate`），创建 5 张运营相关表。
2. **后端**：代码已在仓库，重启 `star-page-backend.service` 加载新路由（`/api/analytics/collect`、`/api/admin/analytics/*`）。
3. **前端**：`npm run build` 后重启 `star-page-frontend.service`（`ExecStartPre` 同步 standalone 静态资源）。
4. **聚合定时器**（首次安装）：
   ```bash
   cp code/systemd/star-page-analytics.{service,timer}.example /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now star-page-analytics.timer
   systemctl start star-page-analytics.service   # 立即补跑昨天+今天
   ```

## 验证结果

| 检查项 | 结果 |
| --- | --- |
| `/ops` | 200 |
| `/admin` | 200 |
| CSS/JS 静态资源 | 200 |
| `POST /api/analytics/collect` | 204 |
| `GET /api/admin/analytics/realtime`（无登录） | 401 |
| `star-page-analytics.service` 手动触发 | 聚合完成 |

## 使用说明

- 运营后台：`/ops`（管理员手机号登录，与 `/admin` 互加入口）
- 定时器：每小时 :05 刷新当天，每日 00:10 补全昨日并重算留存
- 历史回填：`.venv/bin/python -m app.analytics.aggregate --backfill 90`

## 相关文档

- 方案与交付范围：`doc/20260628/ops-backend-design-and-implementation.md`
- 开发者细节：`code/backend/app/analytics/README.md`
- 跨项目方法论：`wiki/product-ops-metrics-and-north-star.md`
