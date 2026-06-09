# 生产 HTTPS 上线与首屏体验优化

## 背景

星页主站 `stars-page.com` 完成域名解析与 SSL 证书采购后，需要把 HTTP 站点升级为 HTTPS，并优化新用户首次访问时的多模型认知。

## 已完成

### HTTPS 与域名

- 采购并部署 DigiCert DV 证书（覆盖 `stars-page.com` 与 `www.stars-page.com`）
- 通过阿里云「数字证书管理服务 → 云服务部署」自动下发到 ECS
- Nginx 终止 TLS，后端/前端仍监听本机 `3000`/`8000`
- `www` 统一 301 到根域 `https://stars-page.com`
- 启用 HSTS（`max-age=31536000; includeSubDomains`）
- 后端公开地址环境变量切换为 `https://stars-page.com`，避免 iframe 预览跨域

### 首屏默认模型

- 新用户首次访问时，默认同时勾选「通义千问 3.7 Max」与「豆包 Seed 2.0 Pro」
- 目的：直观展示「可多模型并行对比」的产品能力

### ICP 备案号展示

- 备案号：粤ICP备2026071100号，链接 `https://beian.miit.gov.cn`
- 仅在 idle 首页底部静态展示，工作区不展示（详见 `doc/20260610/icp-filing-footer-implementation.md`）

## 当前状态

- 站点可通过 `https://stars-page.com` 正常访问
- ICP 备案已通过并在首页展示备案号
- 证书有效期至 2026-12-09，到期前需续费并重新部署
- 技术细节与运维命令见 `wiki/aliyun-ssl-cert-nginx-deployment.md`

## 后续关注

- 证书续期与部署资源包次数
- 浏览器端人工验收：登录、生成、多模型预览、复制链接
