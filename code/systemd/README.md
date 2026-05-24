# Systemd 常驻服务

本目录保存服务器当前运行的 systemd 服务模板：

- `star-page-backend.service.example`：FastAPI 后端，监听 `127.0.0.1:8000`。
- `star-page-frontend.service.example`：Next.js standalone 前端，监听 `127.0.0.1:3000`。

实际服务文件已复制到 `/etc/systemd/system/` 并启用：

```bash
systemctl status star-page-backend.service
systemctl status star-page-frontend.service
```

更新代码后，前端需要先重新构建并复制 standalone 静态资源，再重启服务。

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
