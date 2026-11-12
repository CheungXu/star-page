# Next.js + FastAPI 的 systemd 常驻部署要点

当 MVP 还没有完全切换到 Docker Compose 时，可以先用 systemd 管理 Next.js 和 FastAPI，避免手动后台进程在 SSH 断开或服务器重启后丢失。

## 服务拆分

- 前端服务：运行 Next.js standalone，监听 `127.0.0.1:3000`。
- 后端 web 服务：用 systemd 模板单元 `star-page-web@.service` 开两个实例，分别监听 `127.0.0.1:8000`、`127.0.0.1:8001`，只接请求。
- 后端 worker 服务：`star-page-worker.service`，跑长任务，不监听端口，只开一个。
- Nginx：只暴露公网 `80/443`，按路径转发到前端或后端的 `upstream`；另开一个只监听本机的入口（本项目 `127.0.0.1:8080`），给前端服务端取数用，同样走 `upstream`。

模板单元（文件名带 `@`）的好处：一份文件起多个实例，`%i` 就是端口号，`systemctl restart star-page-web@8000` 单独重启其中一个。

## 关键原则

1. systemd 服务只绑定本机地址，不直接对公网开放应用端口。
2. 后端通过 `EnvironmentFile` 读取真实配置，密钥文件不入库。
3. Next.js standalone 每次重新构建后，要复制 `.next/static` 和 `public` 到 `.next/standalone/`。
4. 如果后续改用 Docker Compose，需要先停止或替换 systemd 服务，避免端口冲突。

## Next.js standalone 静态资源教训

在服务运行中执行 `npm run build` 会重新生成 `.next/standalone`。如果构建后没有重启 `star-page-frontend.service`，运行目录可能缺少 `.next/standalone/.next/static`，表现为首页 HTML 能打开，但 `/_next/static/*.css` 返回 500，页面退化成裸 HTML。

处理原则：

- 构建后必须重启 `star-page-frontend.service`，让 `ExecStartPre` 重新复制 `.next/static` 和 `public`。
- 不能只用首页 `200 OK` 判断部署成功，还要验证 CSS 资源返回 `200 text/css`。

## 代码更新需重启服务（无 `--reload`）

常驻服务直接从仓库目录运行 uvicorn / Next.js standalone，且生产**不开 `--reload`**，只在启动时加载一次代码。

- 改了后端代码（哪怕只是改一行 prompt 文案），必须重启对应进程，否则进程内存里还是旧代码——典型症状：磁盘代码已更新，但线上行为不变（看 `systemctl status` 的 `Active: since` 早于改动时间即可确认）。请求路径上的代码重启 web（`script/restart_web.sh` 逐个重启），生成相关的代码还要重启 worker。
- 多个 web 实例滚动重启时，要等前一个恢复、再等过 nginx `upstream` 的 `fail_timeout`，才重启下一个。nginx 在冷却时间内仍把刚恢复的实例当作不可用，连着重启会出现所有实例同时不可用的空档。
- 前端改动需要先 `next build` 再重启（见上一节静态资源教训）。
- 排查口诀：行为没变先看进程启动时间，而不是反复怀疑代码。

## 域名与生成页预览同源

生成页访问统一走后端 `/p/{conversation_id}/{page_id}` 网关。该响应会带 `Content-Security-Policy: ... frame-ancestors 'self'`，目的是只允许同源主站把生成页嵌入 iframe，避免页面被第三方站点套壳。

因此域名上线后，后端服务里的公开地址必须同步从 IP 切到主域名，例如：

```ini
Environment=PUBLIC_BASE_URL=https://stars-page.com
Environment=FRONTEND_ORIGIN=https://stars-page.com
```

如果仍保留 `PUBLIC_BASE_URL=http://8.138.118.232`，用户通过 `http://stars-page.com/` 或 `http://www.stars-page.com/` 打开主站时，前端 iframe 会加载 `http://8.138.118.232/p/...`，浏览器会把父页面和预览页判定为不同源，表现为“拒绝连接”或预览灰屏，但后端日志里 `/p/...` 可能仍是 `200 OK`。

前端也应把 `/p/...` 类型的生成页链接归一到当前浏览器 origin，再用于 iframe 预览。这样用户从 `stars-page.com` 访问就预览 `stars-page.com/p/...`，从 `www.stars-page.com` 访问就预览 `www.stars-page.com/p/...`；“复制/打开页面”也不会被旧 IP 链接拖回不同源。

排查顺序：

- 看浏览器地址栏当前 origin 和 iframe `src` 是否一致。
- 看 `/p/...` 响应头是否含 `frame-ancestors 'self'`。
- 用 `curl -D - http://域名/p/...` 验证后端是否能返回 HTML；若返回 `200` 但浏览器预览失败，优先怀疑同源/CSP，而不是模型生成失败。
- 修改 systemd 单元里的 `PUBLIC_BASE_URL` / `FRONTEND_ORIGIN` 后，必须 `systemctl daemon-reload` 并重启后端；前端 URL 归一化代码变更则需要 `npm run build` 后重启前端。

## SSE / 长连接与停止超时

后端有 SSE 长连接（生成过程事件流）。老版本 uvicorn 收到 SIGTERM 会等长连接自己断开，systemd 要等到默认 `TimeoutStopSec`（90s）才 SIGKILL。实测 uvicorn 0.47 + Starlette 1.1 会给还在推送的流式响应发断开信号，挂着 SSE 也在 0.2 秒内退出，不需要再加 `--timeout-graceful-shutdown`；换版本后用一个最小 SSE 应用加 `curl -N` 重测一次即可。

处理原则：

- 仍给这类服务显式设较短的 `TimeoutStopSec`（本项目 web 设为 `10s`），防止某个请求卡住时停止拖到 90 秒。
- 日志里出现 `State 'stop-sigterm' timed out. Killing.` 说明有请求没能及时结束，偶发属预期，频繁出现要查是哪个接口。
- 强杀的前提是长任务不依赖进程退出时的收尾：进程内后台任务在退出时不改状态，留给下次启动扫尾重跑或标失败（见 `sse-background-job-streaming.md`）。
- Python 里用 `print` 写的运维日志，进程 stdout 不是终端时会被块缓冲，重启前的几行可能晚出现甚至丢失；关键日志加 `flush=True`。

## 文件描述符上限

systemd 启动的服务默认软上限 `LimitNOFILE` 是 1024（硬上限很高，但 Python 不会自己调到硬上限）。每条 SSE、每条到模型厂商的流式请求、每条 OSS 和数据库连接都占一个描述符，几百个并发浏览者就能碰到 `Too many open files`。滚动重启时流量会全压到剩下那个实例上，更容易碰到。

- 在 unit 的 `[Service]` 里写 `LimitNOFILE=65536`，`daemon-reload` 后重启进程才生效。
- 用 `cat /proc/<PID>/limits | grep 'open files'` 看运行中进程的实际值，不要只看 `systemctl show`。

## Python 异步服务的线程池

`asyncio.to_thread` 用的默认线程池只有 `min(32, CPU 数 + 4)` 个线程，4 核机器上是 8 个。对象存储 SDK、支付 SDK、HTML 解析、图片处理都挤在这 8 个里，慢的一多，快的也要排队。

- 进程启动后用 `loop.set_default_executor(ThreadPoolExecutor(max_workers=32))` 放大默认池。
- 又慢又吃 CPU 的活（完整文档解析）单独给一个小池，用 `loop.run_in_executor(专用池, ...)`，免得占满通用池。
- 线程池不能绕过 GIL：纯 Python 的解析（如 BeautifulSoup 的 `html.parser`）放进线程只是不再卡死事件循环，同一进程里的总吞吐还是一个核。真要多核得多开进程。
- 同步 SDK 返回「流式响应对象」时要特别注意：例如 `oss2` 的 `bucket.get_object()` 只读完响应头就返回，`.read()` 才读正文。只把 `get_object` 放进线程、回到事件循环再 `.read()`，等于在事件循环里同步读网络。要把两步放在同一个线程函数里。
- 同理，SDK 客户端（`oss2.Bucket`）要在进程里复用，每次新建会丢掉连接池、每次都重新握手。连接池大小要不小于线程池大小。

## 常用命令

```bash
systemctl status star-page-worker.service 'star-page-web@*'
systemctl status star-page-frontend.service

script/restart_web.sh                        # 逐个重启 web
systemctl restart star-page-worker.service   # 重启 worker（在跑的任务由启动扫尾处理）
systemctl restart star-page-frontend.service

journalctl -u star-page-worker.service -u 'star-page-web@*' -f
journalctl -u star-page-frontend.service -f
```

## 一次完整发布流程（含迁移与验证）

改了后端代码/技能文件、或新增 SQL 迁移时，按此顺序发布（在仓库根执行）：

```bash
# 0) 若改过 service 单元文件
systemctl daemon-reload

# 1) 数据库迁移（用服务同环境；迁移脚本无"已应用"跟踪，靠 SQL 自身幂等）
cd code/backend && set -a && . ../../config/db.env && . ../../config/oss.env && . ../../config/llm.env && set +a \
  && .venv/bin/python -m app.db.migrate

# 2) 重启后端并验证（生成相关改动才需要重启 worker）
systemctl restart star-page-worker.service
script/restart_web.sh
curl -s http://127.0.0.1:8000/healthz && curl -s http://127.0.0.1:8001/healthz
curl -s http://127.0.0.1:8000/api/skills   # 确认技能数符合预期

# 3) 前端：先 build 再重启（standalone 需复制 .next/static，由 ExecStartPre 完成）
cd ../frontend && npm run build
systemctl restart star-page-frontend.service

# 4) 验证首页与 CSS 资源（不能只看首页 200）
curl -s -o /tmp/idx.html -w "%{http_code}\n" http://127.0.0.1:3000/
css=$(grep -oE '/_next/static/[^"]+\.css' /tmp/idx.html | head -1)
curl -sI "http://127.0.0.1:3000$css" | grep -iE 'HTTP/|content-type'
```

要点：迁移脚本（`app/db/migrate.py`）会把 `migrations/*.sql` 全部按序执行一遍，因此每个 SQL 必须幂等（如 `ADD COLUMN IF NOT EXISTS`）；后端 registry 等进程级缓存只在启动时加载，技能/配置改动必须重启后端，仅改磁盘文件不生效。

## 适用阶段

适合早期 MVP、单机部署、需要快速保证服务常驻的阶段。等应用稳定后，可以再统一切换到 Docker Compose、镜像仓库和更完整的发布流程。