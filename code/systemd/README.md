# Systemd 常驻服务

本目录保存服务器当前运行的 systemd 服务模板：

- `star-page-web@.service.example`：后端 web 实例模板（`APP_ROLE=web`），实际启用 `star-page-web@8000`、`@8001`、`@8002` 三个，Nginx 在它们之间转发。只接请求、推事件流，不跑生成，可以逐个重启。
- `star-page-worker@.service.example`：生成 worker 模板（`APP_ROLE=worker`，`python -m app.worker`），实际启用 `star-page-worker@1` 和 `@2`。认领并跑生成、续期、接手失联 worker 的任务，不对外提供 HTTP。可以多开，名额按库里在跑的任务数算，各实例的 `GENERATION_*` 上限要一致。停止时先排空，`TimeoutStopSec=390s`。
- `star-page-frontend.service.example`：Next.js standalone 前端，监听 `127.0.0.1:3000`。服务端取数走 Nginx 的本机入口 `127.0.0.1:8080`，由它转到各个 web 实例。
- `star-page-analytics.service.example` + `star-page-analytics.timer.example`：运营指标聚合（oneshot + 定时器），把明细埋点幂等聚合进快照表，供运营后台 `/ops` 读取。
- `star-page-backend.service.example`：拆分前的单进程后端（接请求也跑生成），**只在回滚时启用**，不要和 web、worker 同时运行。

实际服务文件已复制到 `/etc/systemd/system/` 并启用：

```bash
systemctl status 'star-page-worker@*' 'star-page-web@*'
systemctl status star-page-frontend.service
systemctl status star-page-analytics.timer
```

各进程的连接池在 unit 里按角色设置：每个 web 常驻 10、突发 10；每个 worker 常驻 20、突发 30。3 个 web 加 2 个 worker 最多 160 条，RDS 上限 2420。

机器是 4 核：3 个 web 加 2 个 worker 刚好每核都有活。再加实例前先看 `[metrics]` 日志（每个进程每分钟一行，见 `code/backend/README.md`）：web 的 `loop_lag_max_ms` 常在 100 以上才值得加 web；worker 的 `running` 常贴着全站上限、或 `loop_lag` 高才值得加 worker。

后端 unit 都设了 `LimitNOFILE=65536`。systemd 默认软上限只有 1024，每条事件流、模型调用、OSS 和数据库连接各占一个。改 unit 后要 `systemctl daemon-reload` 并重启进程，用 `grep 'open files' /proc/<PID>/limits` 确认。

## 改了代码要重启哪个

常驻服务不开 `--reload`，改完代码必须重启。

| 改动 | 重启 |
| --- | --- |
| 接口、分享页、登录、计费展示等请求路径上的代码 | `script/restart_web.sh`（逐个重启所有在跑的 web，不中断服务） |
| 生成相关：prompt、模型调用、资料解析、结算、`generation_service` / `generation_runner` | 再加 `script/restart_worker.sh`（逐个重启 worker，每个先跑完手上的生成再退出，生成不中断；手上有长任务时一个要等几分钟） |
| 模型目录 `config/llm.models.json`、`config/*.env` | 两者都重启 |
| 计费倍率 `config/billing.json` | 不用重启，各进程 5 秒内按文件修改时间重新读取 |
| 前端 | 先 `npm run build`，再 `systemctl restart star-page-frontend.service` |

滚动重启要等前一个 web 恢复、再等过 Nginx 的 `fail_timeout`（5 秒）才重启下一个。否则 Nginx 仍把刚恢复的实例当作不可用，第二个一停就两边同时不可用，场景页等服务端渲染会短暂返回 404。`script/restart_web.sh` 已经按这个顺序做。

更新前端代码后，需要先重新构建，再重启服务，由 `ExecStartPre` 复制 standalone 静态资源。

注意：不要只执行 `npm run build` 而不重启 `star-page-frontend.service`。`next build` 会重建 `.next/standalone`，可能移除 `.next/standalone/.next/static`，导致首页 HTML 正常但 CSS/JS 静态资源 500，页面退化成裸 HTML。构建后应立即重启服务，并检查 `/_next/static/*.css` 返回 `200 text/css`。

## 安装或更新服务

```bash
cp code/systemd/star-page-worker@.service.example /etc/systemd/system/star-page-worker@.service
cp code/systemd/star-page-web@.service.example /etc/systemd/system/star-page-web@.service
cp code/systemd/star-page-frontend.service.example /etc/systemd/system/star-page-frontend.service
systemctl daemon-reload
systemctl enable --now star-page-worker@1.service star-page-worker@2.service
systemctl enable --now star-page-web@8000.service star-page-web@8001.service star-page-web@8002.service
systemctl restart star-page-frontend.service
```

加一个 web：`systemctl enable --now star-page-web@<端口>`，再在 `config/nginx/star-page.conf` 的 `upstream star_page_api` 里加一行同样写法的 `server`，`nginx -t` 后 reload，并把端口加进前端 unit 的 `After=`。加一个 worker：`systemctl enable --now star-page-worker@<编号>`，不用改别处。减少时先停实例再删 Nginx 那一行。

Nginx 需要同时换成 `config/nginx/star-page.conf`（定义 `upstream star_page_api`、埋点限流和 `127.0.0.1:8080` 内部入口）和 `config/nginx/star-page-locations.conf`，`nginx -t` 通过后 `systemctl reload nginx`。

## 新老 Worker 切换规范（重点）

切换 worker 前，先判断本次变更属于哪一类——选错方式会导致在途生成被重复执行甚至重复扣费：

### 1. 日常发版：协调协议不变 → 滚动排空（`script/restart_worker.sh`）

适用：改 prompt、技能、模型调用、解析逻辑、计费公式等业务代码，且认领锁、续期时长、终态归属校验规则不变。

- 直接跑 `script/restart_worker.sh`：先对 `@1` 发 SIGTERM，它停领新活、保持续期与停止响应，等手上的任务和解析跑完（最多 360 秒）再退出，期间 `@2` 照常接新活；`@1` 新进程打出 `[worker] 已启动` 后再同样重启 `@2`。
- **并存窗口注意**：第一个新 worker 启动到最后一个旧 worker 排空退出之间（最长 6 分钟），**新老代码同时在线读写同一个库**。因此涉及表结构或事件 payload 变更时，必须保证新字段向后兼容（可空或有默认值），先跑 `python -m app.db.migrate`，再滚 worker，最后滚 web。

### 2. 协议不兼容升级或回退旧版 → 必须「先停旧、确认归零、再启新」

适用：认领/续期/扫尾规则有不兼容改动（如 2026-11-13 从不续期的单 worker 切到多 worker `worker@` 模板、修改咨询锁键、大幅缩短 `GENERATION_WORKER_LEASE_SECONDS`），或**连代码一起回退到 11-13 之前的版本**。

**严禁新老同时在线的原因**：
1. 老代码不刷 `heartbeat_at`（也无写终态前的行锁归属校验），新 worker 会在 45 秒后把它手上的任务当成失联接手重跑，老 worker 跑完仍照写不误——同一任务生成两个版本、结算两次。
2. 老代码的启动扫尾不看 `heartbeat_at`，一旦在新 worker 还在跑任务时启动老 worker，老 worker 会把所有在跑任务当成中断清掉重跑。
3. 新老并发上限算法不同（老版算进程内存，新版数库里 `running` 行），混跑会使限流失效。

标准切换步骤：

```bash
# 1) 先查库里在途任务与批次解析数
set -a && source config/db.env && set +a
psql -X -At -c "
  SELECT status, coalesce(worker_id, '-'), count(*)
  FROM generation_tasks WHERE status IN ('pending', 'running') GROUP BY 1, 2;
  SELECT parse_status, count(*)
  FROM generation_batches WHERE parse_status = 'running' GROUP BY 1;
"

# 2) 停掉所有旧 worker（新版会自己排空；旧单实例无排空，最好等上面 running=0 再停）
systemctl disable --now star-page-worker.service          # 若从旧单实例切出
# 或：systemctl stop 'star-page-worker@*'                 # 若停多实例模板

# 3) 再次执行第 1 步 SQL，确认 running 任务和 running 解析都为 0（pending 可留给新 worker 领）

# 4) 执行数据库迁移、切换代码与 unit，再启动新 worker
systemctl daemon-reload
systemctl enable --now star-page-worker@1.service star-page-worker@2.service
```

查看日志：

```bash
journalctl -u 'star-page-worker@*' -f
journalctl -u 'star-page-web@*' -f
journalctl -u 'star-page-*' --since -10min -o cat | grep '^\[metrics\]'
journalctl -u star-page-frontend.service -f
```

## 回滚到单进程

```bash
systemctl disable --now 'star-page-worker@*' 'star-page-web@*'
cp code/systemd/star-page-backend.service.example /etc/systemd/system/star-page-backend.service
systemctl daemon-reload
systemctl enable --now star-page-backend.service
```

Nginx 改回只转发 `127.0.0.1:8000`，前端的 `BACKEND_INTERNAL_URL` 改回 `http://127.0.0.1:8000` 后重启前端。迁移 `025`、`026` 只加了可空字段和部分索引，老代码不读它们，不用回退。2026-11-12 切换前的线上配置备份在 `/root/deploy-backup/20261112-web-worker-split/`。

只想退回「单个 worker」（代码不回退）：停掉 `@2`，留 `@1` 即可，新代码一个 worker 也能跑。要连代码一起退回 11-13 之前，用 `/root/deploy-backup/20261113-multi-worker/` 里的 `star-page-worker.service`、`star-page-web@.service`、`star-page-frontend.service` 和 Nginx 配置，切换顺序同上：先停新 worker、确认没有在途任务，再启老的。

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
