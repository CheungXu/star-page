# 前端 Docker 镜像构建与 ACR 发布记录

## 背景

前端新增 `motion` 依赖（首页 ↔ 生成页衔接过渡动画）后，需要重新打包前端镜像并推送到阿里云 ACR。本文记录这次镜像构建与发布过程，以及其中依赖、基础镜像、加速器和 ACR 命名问题的处理。

## 依赖与 Dockerfile

- 新增的 `motion` 已写入 `package.json` / `package-lock.json`。前端 `Dockerfile` 通过 `RUN npm install` 按 lockfile 安装依赖，**无需在 Dockerfile 里单独声明 `motion`**，重新构建即自动带上。
- 新增 `code/frontend/.dockerignore`：排除本地 `node_modules` / `.next`，避免 `COPY . .` 用本地依赖覆盖镜像内 `npm install` 干净安装的结果（构建上下文从含 node_modules 缩到约 624kB）。

## 基础镜像与「免加速」

构建一度卡在基础镜像 `node:22-bookworm-slim`：

1. 本机直连 Docker Hub 拉取超时。
2. 配置阿里云账号镜像加速器（`*.mirror.aliyuncs.com`）后，`hello-world` 能拉，但 `node:22` / `alpine:3.20` 报 `not found`——个人版加速器只对已缓存镜像有效、缺较新 tag，不能当通用 Docker Hub 加速器。
3. 改用全量回源型加速器 DaoCloud（`docker.m.daocloud.io`）成功拉到 `node:22-bookworm-slim`。

为彻底摆脱加速器依赖，把 `node:22-bookworm-slim` 预存到 ACR：`stars-page/node:22-bookworm-slim`（push 到已有命名空间下不存在的仓库时，个人版会自动创建仓库）。

前端 / 顶层 `Dockerfile` 改用 `ARG NODE_IMAGE` 控制基础镜像来源，**默认指向 ACR 预存镜像**，构建免加速。无 ACR 登录的环境：先询问用户登录 ACR，仅当用户明确选择不登录降级时，才用 `--build-arg NODE_IMAGE=node:22-bookworm-slim` 回退 Docker Hub。操作细节见 `code/frontend/README.md`。

### 系统变更

本机 `/etc/docker/daemon.json` 的 `registry-mirrors` 配置为 DaoCloud（原文件备份为 `/etc/docker/daemon.json.bak.<时间戳>`）。这是本机系统配置、不入库；由于 base 镜像已预存 ACR，常规构建已不依赖该加速器，它仅在需要重新从 Docker Hub 拉镜像时兜底。

## ACR 命名演变（勘误）

仓库命名几经反复，最终以控制台为准应为 `stars-page`（与 OSS Bucket `stars-page-demo` 前缀一致）：

| 阶段 | 误用 | 说明 |
| --- | --- | --- |
| 早期 | `stars-page-demo` | 文档初次记录 |
| 中途 | `starts-page`（带 t） | 第二次仍为笔误 |
| 最终 | `stars-page` | 正确，已统一更正全仓库文档 |

注意：OSS Bucket `stars-page-demo`、SSH key、logo 文件名（`stars-page-logo*.png`）是不同的资源，保持不变。

## 镜像清单与 tag 约定

ACR 实例：`crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com`（个人版，华南 3 广州）

- `stars-page/node:22-bookworm-slim`：预存基础镜像。
- `stars-page/stars-page:frontend-<git 短 sha>` + `:frontend-latest`：前端应用镜像。

tag 约定：应用镜像用 `frontend-<git 短 sha>` 标识代码版本，`frontend-latest` 滚动指向最新。本次发布对应 `frontend-1d27699`。

## 与部署的关系

- 当前生产前端用 systemd 跑 Next.js standalone（见 `wiki/systemd-nextjs-fastapi-deployment.md`），尚未切换到容器部署。
- `code/docker-compose.yml` 当前为本地 `build`；如需改用 ACR 镜像部署，可把 `frontend` 的 `build` 换成 `image: crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page/stars-page:frontend-latest`。

## 可复用经验

沉淀到 `wiki/aliyun-mvp-deployment-checklist.md`（ACR 配置 + 常见问题）：个人版镜像加速器局限、基础镜像预存 ACR、Dockerfile 用 `ARG` 控制 base 来源与「先登录、拒绝才降级」约定。
