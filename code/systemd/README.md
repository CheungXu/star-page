# Systemd 常驻服务

本目录保存服务器当前运行的 systemd 服务模板：

- `star-page-backend.service.example`：FastAPI 后端，监听 `127.0.0.1:8000`。
- `star-page-frontend.service.example`：Next.js standalone 前端，监听 `127.0.0.1:3000`。
- `star-page-analytics.service.example` + `star-page-analytics.timer.example`：运营指标聚合（oneshot + 定时器），把明细埋点幂等聚合进快照表，供运营后台 `/ops` 读取。

实际服务文件已复制到 `/etc/systemd/system/` 并启用：

```bash
systemctl status star-page-backend.service
systemctl status star-page-frontend.service
systemctl status star-page-analytics.timer
```

更新代码后，前端需要先重新构建，再重启服务，由 `ExecStartPre` 复制 standalone 静态资源。

注意：不要只执行 `npm run build` 而不重启 `star-page-frontend.service`。`next build` 会重建 `.next/standalone`，可能移除 `.next/standalone/.next/static`，导致首页 HTML 正常但 CSS/JS 静态资源 500，页面退化成裸 HTML。构建后应立即重启服务，并检查 `/_next/static/*.css` 返回 `200 text/css`。

## 安装或更新服务

```bash
cp code/systemd/star-page-backend.service.example /etc/systemd/system/star-page-backend.service
cp code/systemd/star-page-frontend.service.example /etc/systemd/system/star-page-frontend.service
systemctl daemon-reload
systemctl enable --now star-page-backend.service star-page-frontend.service
```

查看日志：

```bash
journalctl -u star-page-backend.service -f
journalctl -u star-page-frontend.service -f
```

## 运营指标聚合定时器

聚合脚本为幂等批处理（`python -m app.analytics.aggregate`），由 timer 周期触发：每小时第 5 分钟刷新当天、每日 00:10 补全昨日，并重算最近 35 天留存 cohort。安装：

```bash
cp code/systemd/star-page-analytics.service.example /etc/systemd/system/star-page-analytics.service
cp code/systemd/star-page-analytics.timer.example /etc/systemd/system/star-page-analytics.timer
systemctl daemon-reload
systemctl enable --now star-page-analytics.timer
```

手动补跑或回填历史（如首次上线后回填 90 天）：

```bash
cd code/backend
.venv/bin/python -m app.analytics.aggregate --backfill 90   # 回填最近 90 天
.venv/bin/python -m app.analytics.aggregate --date 2026-06-27
systemctl start star-page-analytics.service                  # 立即触发一次（昨天+今天）
journalctl -u star-page-analytics.service -f
systemctl list-timers star-page-analytics.timer              # 查看下次触发时间
```

> 注意：聚合脚本只读取明细表、写入快照表，不影响线上请求；与后端服务相互独立，改聚合逻辑无需重启后端。
