# 云资源与服务器运行环境配置记录

## 背景

本次工作围绕自然语言 HTML 页面生成网站的 MVP 落地环境展开，目标是完成阿里云基础资源准备，并验证服务器、对象存储、镜像仓库和数据库之间的基础可用性。

## 已完成事项

### 服务器运行环境

当前阿里云轻量应用服务器已完成基础运行环境配置：

- 系统：Ubuntu 24.04
- 规格：2 vCPU、约 4 GiB 内存、约 50 GiB 系统盘
- Docker：已安装并启用
- Docker Compose：已安装
- Nginx：已安装并启用，当前默认站点可用于 HTTP 健康检查
- UFW：已启用，放行 `22/tcp`、`80/tcp`、`443/tcp`
- SSH：已禁用密码登录，使用密钥登录
- Docker 容器日志：已配置单文件大小与保留数量上限
- 应用日志轮转：已为 `/var/log/star-page/*.log` 准备 logrotate 规则

### OSS

已创建阿里云 OSS Bucket：

- Bucket：`stars-page-demo`
- 地域：华南 3（广州）
- Endpoint：`oss-cn-guangzhou.aliyuncs.com`
- 读写权限：私有
- 阻止公共访问：已开启

真实 OSS 密钥配置放在 `config/oss.env`，该文件已通过 `config/.gitignore` 忽略，不应提交。

### ACR

已创建阿里云 ACR 私有镜像仓库（个人版实例）：

- 地域：华南 3（广州）
- 实例 ID：`crpi-6w1a91eyh3y1vcd9`
- 命名空间：`stars-page`
- 应用镜像仓库：`stars-page/stars-page`
- 基础镜像仓库：`stars-page/node`（预存 `node:22-bookworm-slim`，构建时免加速直接从 ACR 拉）
- 公网 Registry：`crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com`
- 专有网络（VPC）Registry：`crpi-6w1a91eyh3y1vcd9-vpc.cn-guangzhou.personal.cr.aliyuncs.com`（同 VPC 内 ECS 推送更快、不耗公网流量）
- 登录账号：阿里云主账号（邮箱），密码为开通服务时设置的访问凭证密码

> 勘误：仓库命名几经反复，最终以控制台为准应为 `stars-page`（与 OSS Bucket `stars-page-demo` 前缀一致）。早期曾误记为 `stars-page-demo`，中途又一度误用带 t 的 `starts-page`，现统一更正为 `stars-page`，正式使用应用仓库 `stars-page/stars-page`。
> 个人版注意：自 2026-02-01 起个人版按地域隔离，跨地域无法访问，就近使用华南 3（广州）。

当前服务器已完成 ACR 登录。镜像构建、推送与「无 ACR 登录时的登录/降级」流程见 `code/frontend/README.md`。

### RDS

已创建阿里云 RDS PostgreSQL Serverless：

- 地域：华南 3（广州）
- 引擎：PostgreSQL
- 付费类型：Serverless
- 当前连接方式：RDS 内网地址
- 数据库测试账号：已验证可连接默认 `postgres` 数据库

已通过 `script/check_postgres_connection.sh` 验证服务器到 RDS 的连接成功。真实数据库连接信息放在 `config/db.env`，该文件已忽略，不应提交。

### LLM

已完成阿里云百炼 Qwen 的第一轮配置与连通性验证：

- 配置文件：`config/llm.env`
- Provider：`qwen`
- 协议：OpenAI-compatible
- Base URL：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- 模型：`qwen3.7-max`
- 能力验证：已成功发起流式请求，并收到 `reasoning_content` 与正式回复内容

真实 LLM API Key 放在 `config/llm.env`，该文件已忽略，不应提交。

## 本次新增仓库内容

- `code/Dockerfile`：Next.js/Node.js 生产镜像模板，待应用代码初始化后使用。
- `code/llm/`：LLM 请求适配层，先支持 OpenAI 兼容格式和 Anthropic 兼容格式；OpenAI-compatible 路径已预留 Qwen `enable_thinking` 和流式 `reasoning_content` 解析。
- `config/.gitignore`：忽略真实环境变量文件。
- `config/env.example`：应用环境变量模板。
- `config/db.env.example`：PostgreSQL/RDS 连接配置模板。
- `config/llm.env.example`：LLM/Qwen 配置模板。
- `script/check_postgres_connection.sh`：PostgreSQL 连接测试脚本。
- `wiki/aliyun-mvp-deployment-checklist.md`：可复用的阿里云 MVP 部署检查清单。

## 风险与注意事项

- AccessKey、数据库密码、ACR 密码等长期凭证不得写入仓库、文档、镜像或聊天记录。
- 模板文件只能包含变量名和非敏感默认值。
- RDS 白名单应优先放行服务器内网 IP 或最小必要网段，不应开放 `0.0.0.0/0`。
- OSS Bucket 保持私有，公开页面后续通过应用网关、CDN 回源或签名 URL 控制访问。
- 当前 Dockerfile 依赖未来应用具备 `package.json`、构建脚本和 Next.js standalone 输出配置，在应用初始化前不可直接用于成功构建。

## 后续建议

- 创建业务数据库，例如 `stars_page`，避免长期使用默认 `postgres` 数据库承载业务表。
- 初始化应用代码、数据库迁移工具和 ORM。
- 为页面生成流程接入 `code/llm/`，通过统一接口调用模型，不在业务层直接绑定厂商 API。
- 将手工 LLM 连通性验证脚本沉淀为正式脚本，便于后续换模型或排查配置。
- 创建 Docker Compose 部署配置，运行时加载 `config/*.env`。
- 配置正式 Nginx 反向代理和 HTTPS。
- 完成域名、备案、CDN 和页面访问网关配置。
