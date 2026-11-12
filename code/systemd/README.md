# Systemd 常驻服务

本目录保存服务器当前运行的 systemd 服务模板：

- `star-page-web@.service.example`：后端 web 实例模板（`APP_ROLE=web`），实际启用 `star-page-web@8000` 和 `star-page-web@8001` 两个，Nginx 在两者之间转发。只接请求、推事件流，不跑生成，可以逐个重启。
- `star-page-worker.service.example`：生成 worker（`APP_ROLE=worker`，`python -m app.worker`）。跑全部生成、持有排队名额、做启动扫尾，不对外提供 HTTP。**只能开一个**。
- `star-page-frontend.service.example`：Next.js standalone 前端，监听 `127.0.0.1:3000`。服务端取数走 Nginx 的本机入口 `127.0.0.1:8080`，由它转到两个 web 实例。
- `star-page-analytics.service.example` + `star-page-analytics.timer.example`：运营指标聚合（oneshot + 定时器），把明细埋点幂等聚合进快照表，供运营后台 `/ops` 读取。
- `star-page-backend.service.example`：拆分前的单进程后端（接请求也跑生成），**只在回滚时启用**，不要和 web、worker 同时运行。

实际服务文件已复制到 `/etc/systemd/system/` 并启用：

```bash
systemctl status star-page-worker.service 'star-page-web@*'
systemctl status star-page-frontend.service
systemctl status star-page-analytics.timer
```

各进程的连接池在 unit 里按角色设置：每个 web 常驻 10、突发 10；worker 常驻 20、突发 30。

## 改了代码要重启哪个

常驻服务不开 `--reload`，改完代码必须重启。

| 改动 | 重启 |
| --- | --- |
| 接口、分享页、登录、计费展示等请求路径上的代码 | `script/restart_web.sh`（逐个重启两个 web，不中断服务） |
| 生成相关：prompt、模型调用、资料解析、结算、`generation_service` / `generation_runner` | 再加 `systemctl restart star-page-worker.service`。在跑的生成会被打断，由启动扫尾重跑一次，挑没人在生成的时候做 |
| 模型目录 `config/llm.models.json`、`config/*.env` | 两者都重启 |
| 计费倍率 `config/billing.json` | 不用重启，各进程 5 秒内按文件修改时间重新读取 |
| 前端 | 先 `npm run build`，再 `systemctl restart star-page-frontend.service` |

滚动重启要等前一个 web 恢复、再等过 Nginx 的 `fail_timeout`（5 秒）才重启下一个。否则 Nginx 仍把刚恢复的实例当作不可用，第二个一停就两边同时不可用，场景页等服务端渲染会短暂返回 404。`script/restart_web.sh` 已经按这个顺序做。

更新前端代码后，需要先重新构建，再重启服务，由 `ExecStartPre` 复制 standalone 静态资源。

注意：不要只执行 `npm run build` 而不重启 `star-page-frontend.service`。`next build` 会重建 `.next/standalone`，可能移除 `.next/standalone/.next/static`，导致首页 HTML 正常但 CSS/JS 静态资源 500，页面退化成裸 HTML。构建后应立即重启服务，并检查 `/_next/static/*.css` 返回 `200 text/css`。

## 安装或更新服务

```bash
cp code/systemd/star-page-worker.service.example /etc/systemd/system/star-page-worker.service
cp code/systemd/star-page-web@.service.example /etc/systemd/system/star-page-web@.service
cp code/systemd/star-page-frontend.service.example /etc/systemd/system/star-page-frontend.service
systemctl daemon-reload
systemctl enable --now star-page-worker.service star-page-web@8000.service star-page-web@8001.service
systemctl restart star-page-frontend.service
```

Nginx 需要同时换成 `config/nginx/star-page.conf`（定义 `upstream star_page_api`、埋点限流和 `127.0.0.1:8080` 内部入口）和 `config/nginx/star-page-locations.conf`，`nginx -t` 通过后 `systemctl reload nginx`。

查看日志：

```bash
journalctl -u star-page-worker.service -f
journalctl -u 'star-page-web@*' -f
journalctl -u star-page-frontend.service -f
```

## 回滚到单进程

```bash
systemctl disable --now star-page-worker.service star-page-web@8000.service star-page-web@8001.service
cp code/systemd/star-page-backend.service.example /etc/systemd/system/star-page-backend.service
systemctl daemon-reload
systemctl enable --now star-page-backend.service
```

Nginx 改回只转发 `127.0.0.1:8000`，前端的 `BACKEND_INTERNAL_URL` 改回 `http://127.0.0.1:8000` 后重启前端。迁移 `025` 只加了一列可空字段，单进程代码不读它，不用回退。2026-11-12 切换前的线上配置备份在 `/root/deploy-backup/20261112-web-worker-split/`。

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
