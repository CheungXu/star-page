# Systemd 常驻服务

本目录保存服务器当前运行的 systemd 服务模板：

- `star-page-backend.service.example`：FastAPI 后端，监听 `127.0.0.1:8000`。
- `star-page-frontend.service.example`：Next.js standalone 前端，监听 `127.0.0.1:3000`。

实际服务文件已复制到 `/etc/systemd/system/` 并启用：

```bash
systemctl status star-page-backend.service
systemctl status star-page-frontend.service
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
