# Nginx 配置模板

与生产服务器 `/etc/nginx/sites-available/star-page` 保持同步的仓库副本。

## 文件

- `star-page.conf`：主站点（HTTP 跳转、HTTPS、`www` 归一、HSTS）。两个 443 server 开了 HTTP/2（nginx 1.24 写法 `listen 443 ssl http2`），多模型生成和预览同时开很多请求时，不再受 HTTP/1.1 同源 6 条连接的限制。80 端口（IP 直连）仍是 HTTP/1.1。
- `star-page-locations.conf`：共享反向代理规则（`/` → 3000，`/api/`、`/p/` → 8000）。`/api/` 关了 `proxy_buffering`、读超时 420 秒，生成事件流靠后端每 15 秒的心跳保持连接。

## 服务器侧配套

证书与重载脚本不在本目录，位于 ECS：

- `/etc/nginx/ssl/`：证书文件（`cert.pem`、`cert.key`、`chain.pem`、`fullchain.pem`）
- `/etc/nginx/ssl/reload-cert.sh`：阿里云自动部署后的合并与 `nginx reload`

完整流程见 `wiki/aliyun-ssl-cert-nginx-deployment.md`。

## 同步到服务器

```bash
cp config/nginx/star-page-locations.conf /etc/nginx/snippets/
cp config/nginx/star-page.conf /etc/nginx/sites-available/star-page
nginx -t && systemctl reload nginx
```
