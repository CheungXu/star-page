# Next.js + FastAPI 的 systemd 常驻部署要点

当 MVP 还没有完全切换到 Docker Compose 时，可以先用 systemd 管理 Next.js 和 FastAPI，避免手动后台进程在 SSH 断开或服务器重启后丢失。

## 服务拆分

- 前端服务：运行 Next.js standalone，监听 `127.0.0.1:3000`。
- 后端服务：运行 Uvicorn/FastAPI，监听 `127.0.0.1:8000`。
- Nginx：只暴露公网 `80/443`，按路径转发到本机前后端。

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

- 改了后端代码（哪怕只是改一行 prompt 文案），必须 `systemctl restart star-page-backend.service`，否则进程内存里还是旧代码——典型症状：磁盘代码已更新，但线上行为不变（看 `systemctl status` 的 `Active: since` 早于改动时间即可确认）。
- 前端改动需要先 `next build` 再重启（见上一节静态资源教训）。
- 排查口诀：行为没变先看进程启动时间，而不是反复怀疑代码。

## SSE / 长连接与停止超时

后端有 SSE 长连接（生成过程事件流）。uvicorn 收到 SIGTERM 会**优雅关闭、等连接断开**；若客户端连接还挂着，systemd 会等到默认 `TimeoutStopSec`（90s）才 SIGKILL，导致 `systemctl restart` 长时间卡住。

处理原则：

- 给这类服务显式设较短的 `TimeoutStopSec`（本项目后端设为 `10s`），让停止在合理时间内强制收尾。
- 日志里出现 `State 'stop-sigterm' timed out. Killing.` 属预期，不是故障。

## 常用命令

```bash
systemctl status star-page-backend.service
systemctl status star-page-frontend.service

systemctl restart star-page-backend.service star-page-frontend.service

journalctl -u star-page-backend.service -f
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

# 2) 重启后端并验证
systemctl restart star-page-backend.service
curl -s http://127.0.0.1:8000/healthz
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