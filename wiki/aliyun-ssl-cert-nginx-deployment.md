# 阿里云 SSL 证书 + Nginx 自动部署

## 适用场景

在 ECS 上用 Nginx 终止 TLS，并通过阿里云「数字证书管理服务 → 部署服务 → 云服务部署」自动下发与续期证书。

## 服务器侧已就绪项

- 证书目录：`/etc/nginx/ssl/`（权限 `700`）
- 合并并重载脚本：`/etc/nginx/ssl/reload-cert.sh`
- Nginx 站点：`/etc/nginx/sites-available/star-page`（仓库模板见 `config/nginx/`）
- 后端公开地址：`PUBLIC_BASE_URL` / `FRONTEND_ORIGIN` 已指向 `https://stars-page.com`

证书文件约定：

| 文件 | 用途 |
| --- | --- |
| `cert.pem` | 站点证书（阿里云自动写入） |
| `cert.key` | 私钥（阿里云自动写入） |
| `chain.pem` | 中间证书链（阿里云自动写入） |
| `fullchain.pem` | `cert.pem` + `chain.pem` 合并结果，供 Nginx `ssl_certificate` 使用 |

## 阿里云控制台填写

部署配置（第 4 步）：

| 字段 | 值 |
| --- | --- |
| 证书路径 | `/etc/nginx/ssl/cert.pem` |
| 私钥路径 | `/etc/nginx/ssl/cert.key` |
| 证书链路径 | `/etc/nginx/ssl/chain.pem` |
| 重启命令 | `bash /etc/nginx/ssl/reload-cert.sh` |

注意：若「部署资源剩余次数」为 0，需先购买部署资源包。

## 域名与 Nginx

当前 DNS 均已解析到服务器公网 IP：

- `stars-page.com`
- `www.stars-page.com`

Nginx 行为：

- 上述两个域名访问 `http://` 会 301 跳转到 `https://`
- 直接用 IP 访问 `http://` 仍可用（便于排障，不强制 HTTPS）

## 部署后验证

```bash
ls -la /etc/nginx/ssl/
bash /etc/nginx/ssl/reload-cert.sh
curl -I https://stars-page.com/
curl -I http://stars-page.com/    # 应 301 到 https
openssl s_client -connect stars-page.com:443 -servername stars-page.com </dev/null 2>/dev/null | openssl x509 -noout -dates -subject
```

浏览器应不再提示自签名证书；若 `www` 访问报证书域名不匹配，说明证书未包含 `www`，需单独申请或改 DNS 只保留一个主域名。

## 首次上线前的占位证书

在阿里云正式证书下发前，服务器会临时使用自签名证书，仅用于让 Nginx 443 配置通过校验。正式证书部署成功并执行 `reload-cert.sh` 后会被覆盖。

## 部署后检查清单（2026-06-10 已完成）

- [x] 阿里云已将 `cert.pem` / `cert.key` / `chain.pem` 写入 `/etc/nginx/ssl/`
- [x] 执行 `bash /etc/nginx/ssl/reload-cert.sh` 合并 `fullchain.pem` 并重载 Nginx
- [x] `https://stars-page.com` 与 `https://www.stars-page.com` 均返回 200，浏览器信任链正常（DigiCert DV）
- [x] 证书 SAN 覆盖 `stars-page.com` 与 `www.stars-page.com`，有效期至 **2026-12-09**
- [x] `http://域名` 301 跳转 HTTPS；`http://公网IP` 仍可访问（排障用）
- [x] 静态资源 `/_next/static/*.css` 经 HTTPS 返回 200
- [x] 后端 `PUBLIC_BASE_URL` / `FRONTEND_ORIGIN` 已为 `https://stars-page.com`

## 后续建议（非必须）

1. **续期**：证书 2026-12-09 到期前在阿里云完成续费，并确认自动部署任务仍绑定本机（或再次手动部署 + 执行 `reload-cert.sh`）。
2. **部署资源包**：关注剩余次数；续期部署会消耗次数。
3. **主域名规范**：`www.stars-page.com` 已 301 到 `https://stars-page.com`（HTTP/HTTPS 均生效）。
4. **HSTS**：主站已启用 `Strict-Transport-Security: max-age=31536000; includeSubDomains`。
5. **清理备份文件（可选）**：阿里云部署留下的 `*.bak` 在 `/etc/nginx/ssl/`，确认无误后可删除。
