# 自然语言 HTML 页面生成网站架构规划

## 1. 背景与目标

本项目计划建设一个网站，用户可以通过自然语言创建 HTML 展示页面。每个用户创建的页面都可以获得一个独立链接，用于对外展示或私下分享。

> 更新说明（20260602）：本文为 20260522 的初始规划，其中"第一版禁用 JavaScript、靠清洗去掉危险代码"已调整。生成页现已支持展示型 CSS/JS，安全改为"隔离优先"（沙箱 CSP + sandboxed iframe + 默认禁外部网络），详见 `wiki/generated-page-js-sandbox-and-security.md` 与 `doc/20260602/generated-page-js-css-sandbox-record.md`。

当前阶段目标是先完成整体架构规划，不进入开发实现。规划重点包括：

- 支持多用户、多页面的页面生成、存储、访问与展示。
- 避免将用户页面直接承载在单台轻量服务器上，降低服务器负载风险。
- 架构优先使用阿里云落地，但保留跨云厂商迁移能力。
- 支持类似共享文档的权限管理：仅自己可见、指定人可见、所有人可见。
- 第一版先做个人用户产品，不引入团队、组织、多租户空间等复杂能力。

## 2. 已确认产品边界

### 2.1 第一版范围

- 用户通过自然语言生成页面。
- 生成结果为纯 HTML 展示页。
- 第一版不允许用户页面执行 JavaScript。
- 第一版不做动态后端交互能力，例如表单提交、评论、支付、用户自定义接口等。
- 第一版登录方式使用邮箱 + 密码。
- 第一版支持个人用户，不支持团队空间。
- 第一版部署优先使用阿里云。
- 架构设计需要保持通用，避免与单一云厂商深度绑定。

### 2.2 需要预留的扩展空间

- 未来可能支持安全受控的 JavaScript。
- 未来可能支持表单、组件交互、页面数据源等动态能力。
- 未来可能支持手机号登录、第三方登录、微信生态登录。
- 未来可能支持团队、组织、协作编辑、角色权限。
- 未来可能迁移到腾讯云、AWS、Cloudflare、火山引擎等其他云厂商。

## 3. 总体架构

整体采用“控制面 + 静态承载面”分离的架构。

控制面负责：

- 用户登录与账号管理。
- 页面创建、编辑、版本管理。
- AI 生成任务管理。
- 页面权限配置与访问鉴权。
- 页面元数据管理。

静态承载面负责：

- 存储生成后的 HTML 文件。
- 通过 CDN 承载公开页面访问流量。
- 为私有页面提供受控访问能力。

推荐整体结构：

```text
用户浏览器
  -> 主站 Web App
  -> API 服务
  -> 数据库
  -> AI 生成 Worker
  -> 对象存储
  -> CDN / 页面访问网关
```

建议拆分两个域名：

```text
app.example.com
  主站、登录、页面管理、页面创建、权限设置

pages.example.com
  用户生成页面的访问入口
```

主站域名和页面域名分离，可以降低用户生成 HTML 对主站登录态和业务接口的安全影响。

## 4. 核心模块设计

### 4.1 Web App

Web App 面向终端用户，提供：

- 注册、登录、退出登录。
- 页面列表。
- 页面创建入口。
- 自然语言输入框。
- 生成状态展示。
- 页面预览。
- 页面权限设置。
- 页面链接复制。

第一版可以使用全栈框架实现，例如 Next.js。也可以使用前后端分离架构，但 MVP 阶段不建议过早拆分太多服务。

### 4.2 API 服务

API 服务负责核心业务逻辑：

- 用户注册与登录。
- Session 管理。
- 页面元数据增删改查。
- 页面权限校验。
- 生成任务创建。
- 页面版本切换。
- 页面访问路由与鉴权。

API 服务应尽量避免直接承载大量静态页面流量。公开页面应交给 CDN，私有页面第一版可以由 API 服务鉴权后返回，后续再升级为边缘鉴权或签名 URL。

### 4.3 AI 生成 Worker

AI 生成 Worker 负责异步生成 HTML：

```text
用户提交 prompt
  -> API 创建生成任务
  -> Worker 拉取任务
  -> 调用大模型生成 HTML
  -> 清洗 HTML
  -> 上传对象存储
  -> 更新 page_versions
  -> 标记任务完成
```

生成任务建议异步处理，避免用户请求长时间阻塞。MVP 阶段可以先使用数据库任务表轮询，后续再替换为专业队列。

### 4.4 对象存储

生成后的 HTML 文件不建议长期存储在轻量服务器本地，应存储到对象存储中。

推荐存储结构：

```text
pages/{page_id}/versions/{version_id}/index.html
```

第一版只生成纯 HTML，因此可以先只存 `index.html`。未来支持图片、CSS、附件后，可以扩展为：

```text
pages/{page_id}/versions/{version_id}/index.html
pages/{page_id}/versions/{version_id}/assets/{asset_name}
```

对象存储 Bucket 默认应设置为私有，不建议直接公开整个 Bucket。

### 4.5 CDN

公开页面可以通过 CDN 缓存，降低源站压力。

私有页面、指定人可见页面不应直接暴露永久公共 CDN 地址，否则链接被转发后可能绕过权限系统。

CDN 策略建议：

- `public` 页面：允许 CDN 缓存。
- `private` 页面：不直接公开对象存储地址。
- `restricted` 页面：必须经过权限校验。

### 4.6 页面访问网关

页面统一通过 `pages.example.com/p/{page_id}` 访问。

访问流程：

```text
用户打开页面链接
  -> 页面访问网关查询页面元数据
  -> 判断 visibility
  -> 若 public，返回公开页面或跳转 CDN 地址
  -> 若 private/restricted，检查登录态与权限
  -> 鉴权通过后返回 HTML
  -> 鉴权失败则跳转登录或显示无权限
```

MVP 阶段页面访问网关可以由 API 服务承担。后续访问量上升后，可以迁移到边缘函数、API 网关、CDN 私有鉴权或短期签名 URL。

## 5. 阿里云落地方案

### 5.1 阿里云产品映射

| 通用能力 | 阿里云建议产品 | 可替代产品 |
| --- | --- | --- |
| 应用服务 | ECS、SAE、函数计算 | 腾讯云 CVM/轻量/SCF、AWS ECS/Lambda |
| 数据库 | RDS PostgreSQL 或 RDS MySQL | 腾讯云数据库、AWS RDS、Supabase |
| 对象存储 | OSS | 腾讯云 COS、AWS S3、Cloudflare R2 |
| CDN | 阿里云 CDN | 腾讯云 CDN、Cloudflare CDN |
| 队列 | 云消息队列、消息服务 MNS | 腾讯云 TDMQ、AWS SQS、Redis/BullMQ |
| 内容安全 | 内容安全服务 | 腾讯云内容安全、第三方审核服务 |
| 日志监控 | SLS、云监控 | 腾讯云 CLS、Prometheus、Grafana |

### 5.2 MVP 部署建议

第一版可以使用较简单的部署方式：

```text
ECS / SAE
  - Web App
  - API 服务
  - AI 生成 Worker

RDS
  - 用户、页面、权限、版本、任务数据

OSS
  - 生成后的 HTML 文件

CDN
  - 公开页面加速
```

如果希望减少服务器运维，优先考虑 SAE。如果希望控制力更强、成本更直观，可以先用 ECS。

### 5.3 轻量服务器配置与镜像选择

如果第一版先使用阿里云轻量应用服务器承载控制面，建议将其定位为 Web App、API 服务、AI 生成 Worker 的运行环境，不负责长期承载用户生成页面文件。

推荐起步配置：

```text
开发 / 小规模内测：
  2 vCPU
  2 GiB 内存
  40 GiB 系统盘

正式 MVP 上线更稳妥：
  2 vCPU
  4 GiB 内存
  40-80 GiB 系统盘
```

该配置成立的前提是：

- 用户生成的 HTML 存放在 OSS。
- 公开页面访问走 CDN。
- 数据库使用 RDS，不与应用混跑在轻量服务器上。
- AI 生成调用外部模型 API，不在服务器本机运行大模型。
- 服务器日志配置轮转，避免系统盘被日志打满。

镜像推荐优先级：

```text
第一推荐：系统镜像 Ubuntu LTS + 自行安装 Docker / Docker Compose
第二推荐：系统镜像 Debian + 自行安装 Docker / Docker Compose
第三推荐：Docker 应用镜像
第四推荐：Alibaba Cloud Linux + 自行安装 Docker / Docker Compose
```

选择 Ubuntu LTS 的主要原因：

- Linux 生态通用，资料丰富。
- Docker、Node.js、Nginx、Caddy 等部署路径成熟。
- 后续从阿里云迁移到其他云厂商时更容易复用部署脚本。
- 环境更干净，避免应用镜像带来不必要的预装组件。

不建议将以下应用镜像作为本项目的主部署环境：

- WordPress、WooCommerce、Drupal、Typecho、Halo：更适合 CMS、博客或商城。
- NextCloud、Cloudreve：更适合网盘，不适合替代 OSS。
- LAMP、LNMP：更偏 PHP 传统网站栈，与当前推荐的 Next.js / Node.js / Docker 方向不匹配。
- 宝塔面板：上手简单，但会增加额外管理入口和安全面。
- OpenClaw、CoPaw、ZeroClaw、Hermes Agent：更偏 AI Agent 或平台工具，不适合作为正式网站运行环境。

如果需要图形化管理服务器，可以考虑 1Panel 辅助管理 Docker，但业务部署仍建议以 Docker Compose 配置为准，避免强依赖面板生成的隐式配置。

### 5.4 当前轻量服务器资源记录

当前已创建一台阿里云轻量应用服务器，可作为第一版控制面运行环境。

```text
云厂商：阿里云
产品：轻量应用服务器
地域：华南 3（广州）
实例名称：Ubuntu-cln
实例规格：通用型
实例状态：运行中
镜像：Ubuntu 24.04
CPU：2 vCPU
内存：4 GiB
系统盘：50 GiB
峰值公网带宽：200 Mbps
公网 IP：8.138.118.232
私网 IP：172.19.39.58
到期时间：2026-06-22 23:59:59
创建时间：2026-05-22 20:28:13
```

后续初始化时建议优先完成：

- 设置 SSH 密钥登录。
- 如无必要，关闭 SSH 密码登录。
- 安全组仅开放 `22`、`80`、`443`，其中 `22` 优先限制为个人固定 IP 访问。
- 安装 Docker 与 Docker Compose。
- 配置 Nginx 或 Caddy 作为反向代理。
- 配置 HTTPS 证书。
- 配置日志轮转，避免系统盘被日志打满。
- 将数据库放在 RDS，将生成 HTML 放在 OSS，不长期存储在本机。

### 5.5 后续云原生扩展

当访问量或生成任务增加后，可以逐步拆分：

- Web App 与 API 服务独立部署。
- AI 生成 Worker 独立扩容。
- 生成任务从数据库轮询迁移到消息队列。
- 页面访问网关迁移到函数计算或边缘节点。
- 公开页面全面走 CDN。
- 私有页面使用短期签名 URL 或 CDN 私有鉴权。

## 6. 跨云迁移设计

虽然第一版使用阿里云，但业务代码应围绕通用接口设计，避免直接把阿里云 SDK 调用散落在业务逻辑中。

### 6.1 云能力抽象

建议在代码中抽象以下接口：

```text
StorageProvider
  putObject()
  getObject()
  deleteObject()
  createSignedUrl()

QueueProvider
  enqueue()
  dequeue()
  ack()
  retry()

ContentSafetyProvider
  checkText()
  checkHtml()

EmailProvider
  sendVerificationEmail()
  sendResetPasswordEmail()
```

业务服务只依赖这些接口，不直接依赖 OSS、COS、S3 的具体实现。

### 6.2 配置隔离

云厂商相关配置应通过环境变量或配置文件注入，例如：

```text
CLOUD_PROVIDER=aliyun
OBJECT_STORAGE_BUCKET=...
OBJECT_STORAGE_REGION=...
CDN_BASE_URL=...
```

迁移云厂商时，优先替换 provider 实现与配置，而不是重写业务逻辑。

### 6.3 数据模型保持中立

数据库中不建议存储强绑定云厂商的完整 URL。推荐存储中立的 `storage_key`：

```text
storage_key = pages/{page_id}/versions/{version_id}/index.html
```

访问时再由 StorageProvider 或 CDN 配置生成实际访问地址。

## 7. 权限体系设计

### 7.1 权限模式

第一版支持三种可见性：

```text
private
  仅自己可见

restricted
  指定注册用户可见

public
  所有人可见
```

### 7.2 角色设计

第一版建议只实现最小角色：

```text
owner
  页面所有者，拥有管理权限

viewer
  可查看页面
```

后续可以扩展：

```text
editor
  可编辑页面

commenter
  可评论

admin
  团队或组织管理员
```

### 7.3 指定人可见

“指定人可见”第一版通过已注册用户指定。

用户体验建议：

- 页面所有者在权限设置中输入对方邮箱。
- 系统查询该邮箱是否已注册。
- 若已注册，则添加该用户为 viewer。
- 若未注册，第一版可以提示“该用户尚未注册”，暂不支持邀请。

底层权限表保存 `user_id`，不要直接保存邮箱作为权限主体。邮箱可以变化，`user_id` 才是稳定身份。

### 7.4 访问判断逻辑

页面访问时按以下顺序判断：

```text
如果当前用户是 owner，允许访问
否则如果页面 visibility = public，允许访问
否则如果页面 visibility = restricted 且当前用户在权限表中，允许访问
否则拒绝访问
```

对于 `private` 页面，只有 owner 可以访问。

## 8. 登录与账号体系

第一版使用邮箱 + 密码登录，不做手机号验证码。

### 8.1 用户表

建议用户表包含：

```text
users
- id
- email
- password_hash
- display_name
- email_verified
- created_at
- updated_at
```

### 8.2 密码安全

密码不能明文存储，应使用成熟算法：

- Argon2id，优先推荐。
- bcrypt，也可以接受。

### 8.3 登录态

登录态建议使用服务端 Session Cookie。

基础要求：

- Cookie 设置 `HttpOnly`。
- 生产环境设置 `Secure`。
- 设置合理的 `SameSite`。
- 页面承载域名不要共享主站敏感 Cookie。

### 8.4 邮箱验证与找回密码

MVP 可以先实现基础注册登录，但正式上线建议补齐：

- 邮箱验证。
- 找回密码。
- 修改密码。
- 账号注销。
- 登录失败频控。

## 9. 数据模型草案

### 9.1 users

```text
users
- id
- email
- password_hash
- display_name
- email_verified
- created_at
- updated_at
```

### 9.2 pages

```text
pages
- id
- owner_user_id
- title
- visibility
- status
- current_version_id
- created_at
- updated_at
- deleted_at
```

### 9.3 page_versions

```text
page_versions
- id
- page_id
- version_number
- prompt
- storage_key
- status
- created_at
```

`status` 可选：

```text
generating
ready
failed
archived
```

### 9.4 page_permissions

```text
page_permissions
- id
- page_id
- user_id
- role
- created_at
```

建议约束：

```text
unique(page_id, user_id)
```

### 9.5 generation_tasks

```text
generation_tasks
- id
- page_id
- requested_by_user_id
- prompt
- status
- error_message
- retry_count
- created_at
- started_at
- finished_at
```

`status` 可选：

```text
pending
running
succeeded
failed
cancelled
```

## 10. 页面生成与发布流程

### 10.1 创建页面

```text
用户输入自然语言
  -> API 创建 pages 记录
  -> API 创建 generation_tasks 记录
  -> 返回页面生成中状态
```

### 10.2 生成页面

```text
Worker 获取 pending 任务
  -> 调用大模型生成纯 HTML
  -> 清洗 HTML
  -> 上传对象存储（阿里云落地时使用 OSS）
  -> 创建 page_versions 记录
  -> 更新 pages.current_version_id
  -> 标记任务 succeeded
```

### 10.3 访问页面

```text
用户访问 pages.example.com/p/{page_id}
  -> 查询 pages
  -> 校验 visibility 和权限
  -> 查询 current_version
  -> 获取 storage_key
  -> 返回 HTML 或跳转 CDN 地址
```

## 11. HTML 安全策略

因为用户生成的是 HTML，即使第一版不允许 JS，也需要做安全处理。

第一版应至少限制：

- 移除 `<script>` 标签。
- 移除 `onload`、`onclick` 等所有事件属性。
- 禁止 `javascript:` 链接。
- 限制 `<iframe>`。
- 限制 `<form>`。
- 限制外部资源引用。
- 设置 Content Security Policy。

建议 CSP 初始策略：

```text
default-src 'none';
img-src https: data:;
style-src 'unsafe-inline';
font-src https: data:;
base-uri 'none';
form-action 'none';
frame-ancestors 'none';
script-src 'none';
```

后续如果支持 JS，应使用独立沙箱域名、iframe sandbox、资源隔离和更严格的权限边界，不应直接在主站上下文中执行用户代码。

## 12. 页面承载策略

### 12.1 public 页面

公开页面可以充分利用 CDN：

```text
pages.example.com/p/{page_id}
  -> 校验页面存在且 public
  -> 返回 CDN 缓存页面
```

可选实现：

- API 返回 HTML。
- API 302 跳转到 CDN 地址。
- CDN 回源到对象存储。
- CDN 边缘函数做路由与鉴权。

MVP 可以优先使用 API 返回或跳转，后续再优化。

### 12.2 private 页面

仅自己可见页面必须经过登录态校验：

```text
访问页面
  -> 未登录则跳转登录
  -> 已登录但非 owner 则拒绝
  -> owner 访问则返回 HTML
```

### 12.3 restricted 页面

指定人可见页面必须经过权限表校验：

```text
访问页面
  -> 未登录则跳转登录
  -> 已登录则检查 page_permissions
  -> 命中 viewer 权限则返回 HTML
  -> 未命中则拒绝
```

### 12.4 私有页面扩容方案

当私有页面访问压力变大时，可以从“API 代理返回 HTML”升级为：

- 短期签名 URL。
- CDN 私有鉴权。
- 边缘函数鉴权。
- 页面内容加密后由授权接口发放解密密钥。

MVP 不建议过早做复杂方案，但数据模型和访问入口需要预留升级空间。

## 13. 内容审核与合规

国内云上线需要考虑：

- 域名备案。
- CDN 域名备案。
- 用户协议。
- 隐私政策。
- 生成内容审核。
- 公开页面举报入口。
- 管理后台下架能力。

第一版即使不做完整后台，也建议至少预留：

```text
pages.status
  active
  suspended
  deleted
```

当页面被举报或审核不通过时，可以将页面状态改为 `suspended`。

## 14. 运维与监控

MVP 阶段建议关注：

- API 请求量。
- 页面访问量。
- AI 生成成功率。
- AI 生成耗时。
- Worker 队列积压。
- OSS 请求量与流量。
- CDN 命中率。
- 登录失败次数。
- 权限拒绝次数。

日志中不要记录明文密码、完整 Session、敏感 Cookie。

## 15. MVP 开发里程碑

### 阶段 1：基础账号与页面管理

- 邮箱 + 密码注册登录。
- 页面列表。
- 创建页面入口。
- 页面详情页。

### 阶段 2：HTML 生成与存储

- 创建生成任务。
- Worker 调用模型生成纯 HTML。
- HTML 安全清洗。
- 上传 OSS。
- 生成版本记录。

### 阶段 3：页面访问与权限

- `private`、`restricted`、`public` 三种 visibility。
- 指定已注册用户可见。
- 页面统一访问链接。
- 私有页面鉴权返回。
- 公开页面 CDN 缓存。

### 阶段 4：上线基础能力

- 邮箱验证。
- 找回密码。
- 内容下架状态。
- 基础日志与监控。
- 域名、备案、CDN、HTTPS 配置。

## 16. 待进一步确认的问题

后续进入开发前，还需要确认：

- 技术栈是否使用 Next.js。
- 数据库选 PostgreSQL 还是 MySQL。
- 阿里云部署选择轻量应用服务器、ECS 还是 SAE。
- AI 模型供应商选择。
- 是否第一版就做邮箱验证。
- 公开页面是否需要 SEO 支持。
- 页面是否需要保留历史版本回滚能力。
- 是否需要管理后台处理违规页面。

