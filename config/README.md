# config

配置文件目录，保存各类配置信息。

## 当前服务器运行环境

已在当前 Ubuntu 24.04 服务器完成基础运行环境配置：

- Docker：已安装并启用，版本 `29.1.3`
- Docker Compose：已安装，版本 `2.40.3`
- Nginx：已安装并启用，当前默认站点可用于基础 HTTP 健康检查
- UFW：已启用，放行 `22/tcp`、`80/tcp`、`443/tcp`
- SSH：已禁用密码登录，当前允许 root 密钥登录
- Docker 日志：已通过 `/etc/docker/daemon.json` 限制单个容器日志文件最大 `10m`，保留 `3` 个文件
- 应用日志：已新增 `/etc/logrotate.d/star-page-app`，未来 `/var/log/star-page/*.log` 会按天轮转并压缩

后续正式部署前仍需补齐：

- 域名解析与备案
- HTTPS 证书
- 正式 Nginx 反向代理站点配置与进程常驻方案
- Docker Hub 镜像拉取加速器，或改用阿里云 ACR 镜像
- CDN、监控、备份等生产配置

## MVP 应用端口

当前 MVP 采用 Next.js 前端 + FastAPI 后端：

```text
公网开放：
80   Nginx HTTP
443  Nginx HTTPS
22   SSH，建议后续限制来源 IP

服务器内部：
3000 Next.js frontend
8000 FastAPI backend
```

`3000` 和 `8000` 不需要在阿里云防火墙对公网开放，由 Nginx 反向代理转发：

```text
/      -> 127.0.0.1:3000
/api/  -> 127.0.0.1:8000
/p/    -> 127.0.0.1:8000
```

## 运行环境变量

真实运行环境变量约定放在 `config/.env` 或按用途拆分为 `config/*.env`，模板文件为 `config/env.example`。

注意：

- `config/.env`、`config/.env.*`、`config/*.env` 已被 `config/.gitignore` 忽略，不应提交到 Git。
- 当前 OSS 真实配置文件为 `config/oss.env`，该文件包含密钥，不读取、不提交。
- RDS 真实配置文件建议使用 `config/db.env`，模板文件为 `config/db.env.example`。
- LLM 真实配置建议放在 `config/llm.env`，模板文件为 `config/llm.env.example`；也可以统一放入 `config/.env`，不要提交真实 API Key。
- `config/env.example` 只能保留变量名和非敏感默认值，不要写入 AccessKey、数据库密码、AI Key、Session Secret 等真实密钥。

## LLM 模型请求

LLM 请求层位于 `code/llm/`，业务代码通过统一 `LlmClient` 调用模型。

当前支持两类主流协议：

- `openai`：OpenAI Chat Completions 兼容格式。
- `anthropic`：Anthropic Messages 兼容格式。

推荐环境变量：

```text
LLM_PROVIDER=
LLM_PROTOCOL=
LLM_BASE_URL=
LLM_MODEL=
LLM_API_KEY=
LLM_TIMEOUT_MS=60000
LLM_ANTHROPIC_VERSION=2023-06-01
LLM_ENABLE_THINKING=
LLM_OPENAI_EXTRA_BODY_JSON=
```

后续接入新模型时，优先通过 `LLM_PROTOCOL` 判断其兼容 OpenAI 还是 Anthropic 格式；只有协议差异较大时再新增适配器。

阿里云百炼 Qwen 配置示例见 `config/llm.env.example`。DashScope / 百炼 API Key 写在真实的 `config/llm.env` 中：

```text
LLM_API_KEY=
```

Qwen 的深度思考开关通过 `LLM_ENABLE_THINKING=true` 配置，底层会作为 `enable_thinking` 合并到 OpenAI-compatible 请求体。

当前服务器已通过 `config/llm.env` 成功调用 Qwen 百炼接口，流式响应中可收到 `reasoning_content` 和正式回复内容。

## 阿里云 OSS

当前已创建 OSS Bucket：

- Bucket 名称：`stars-page-demo`
- 地域：`华南3（广州）`
- Endpoint：`oss-cn-guangzhou.aliyuncs.com`
- 读写权限：私有
- 阻止公共访问：已开启

建议应用环境变量：

```text
OBJECT_STORAGE_PROVIDER=aliyun
OBJECT_STORAGE_BUCKET=stars-page-demo
OBJECT_STORAGE_REGION=cn-guangzhou
OBJECT_STORAGE_ENDPOINT=oss-cn-guangzhou.aliyuncs.com
```

AccessKey 不写入仓库。生产环境应使用 RAM 用户最小权限或实例 RAM 角色，并通过服务器 `.env` 或部署平台环境变量注入。

## 阿里云 ACR

当前已创建 ACR 镜像仓库：

- 地域：`华南3（广州）`
- 命名空间：`stars-page-demo`
- 仓库名称：`stars-page-demo`
- 仓库类型：私有
- 摘要：`demo`
- 公网 Registry：`crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com`
- 镜像地址：`crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page-demo/stars-page-demo`
- 当前服务器登录状态：已登录

登录 ACR 时不要在聊天或文档里保存密码。建议在服务器交互式执行：

```bash
docker login --username=<阿里云账号或RAM用户名> crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com
```

当前 MVP 拆分为 frontend/backend 两个镜像。后续推送 ACR 前，建议为两个服务分别建仓库或使用不同 tag：

```bash
docker build -t star-page-frontend:latest code/frontend
docker build -t star-page-backend:latest code/backend
```

当前阶段先使用“本地仓库”方式更稳妥。等 `Dockerfile`、构建流程、CI/CD 和运行时环境变量方案稳定后，再切换到 GitHub 自动构建。

## 阿里云数据库

图中产品建议选择 `云数据库 RDS`。

第一版推荐使用 `RDS PostgreSQL`：

- 当前业务是典型关系型数据：用户、页面、版本、权限、生成任务。
- PostgreSQL 对 JSON、索引、约束、事务支持成熟，适合后续页面元数据和任务状态扩展。
- 与常见 Node.js/Next.js ORM、迁移工具兼容性好。

不建议 MVP 阶段选择 PolarDB、OceanBase、数据仓库、ClickHouse、Tair、MongoDB 等产品。这些更适合高并发扩展、分析型、缓存或非关系型场景，当前会增加成本和复杂度。

RDS Serverless 适合作为当前 MVP 起步方案：

- 当前访问量不确定，Serverless 可以按实际负载弹性计费，前期成本更可控。
- 对个人用户 MVP 来说，先用 `0.5` 最小 RCU 起步合理。
- 最大 RCU 不建议一开始开太高，避免异常流量导致费用不可控；可以先设 `2` 到 `4`，后续根据监控调高。

建议起步配置：

- 地域：`华南3（广州）`，与服务器、OSS 保持一致
- 引擎：`PostgreSQL`
- 付费类型：`Serverless`
- 版本：优先选择控制台推荐的稳定版本；如果 `PostgreSQL 18` 太新且生态适配不确定，可以选更常见的稳定版本
- 产品系列：MVP 可先选基础系列，正式生产再考虑高可用系列
- 存储类型：MVP 可先选高性能云盘
- 弹性策略：先选不强制执行或保守弹性，后续根据压测与监控调整
- 网络：优先选择能与当前服务器互通的 VPC
- 访问：优先走内网地址；如果轻量应用服务器无法进入同一 VPC，再考虑开启公网地址并只把白名单限制为服务器公网 IP
- 账号：应用单独创建数据库账号，不使用高权限账号直连业务

如果控制台提示 `SLR 不存在`，需要先按提示创建 RDS Serverless 服务关联角色。这是阿里云让 RDS 自动管理弹性资源所需的授权。

创建 RDS 后，连接测试需要以下信息：

- RDS 内网连接地址，优先使用内网 Endpoint
- 端口，PostgreSQL 默认 `5432`
- 数据库名
- 应用数据库账号
- 应用数据库密码

真实信息写入 `config/db.env` 后，可在服务器执行：

```bash
bash script/check_postgres_connection.sh
```

当前服务器已通过 RDS 内网地址连通 PostgreSQL，测试账号可访问 `postgres` 数据库。

MVP 业务表已统一放在独立数据库 `stars_page`。应用账号需要具备在业务库 `public` schema 下创建表的权限；如果迁移时报 `permission denied for schema public`，用高权限账号参考 `script/prepare_rds_database.sql` 先完成授权。

当前 `stars_page` 迁移已成功执行，默认测试用户 `default_test` 已初始化。后续不要长期把业务表放在默认 `postgres` 数据库。
