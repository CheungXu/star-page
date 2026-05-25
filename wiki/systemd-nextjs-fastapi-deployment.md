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

## 常用命令

```bash
systemctl status star-page-backend.service
systemctl status star-page-frontend.service

systemctl restart star-page-backend.service star-page-frontend.service

journalctl -u star-page-backend.service -f
journalctl -u star-page-frontend.service -f
```

## 适用阶段

适合早期 MVP、单机部署、需要快速保证服务常驻的阶段。等应用稳定后，可以再统一切换到 Docker Compose、镜像仓库和更完整的发布流程。
