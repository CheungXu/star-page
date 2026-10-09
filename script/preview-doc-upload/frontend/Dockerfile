# syntax=docker/dockerfile:1

# 基础镜像默认指向阿里云 ACR（已预先上传 node:22-bookworm-slim），构建时不经过 Docker Hub / 镜像加速器。
# 无 ACR 登录的环境，应先询问用户登录 ACR；用户选择不登录降级时，再回退 Docker Hub：
#   docker build --build-arg NODE_IMAGE=node:22-bookworm-slim ...
ARG NODE_IMAGE=crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page/node:22-bookworm-slim

FROM ${NODE_IMAGE} AS deps
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

FROM ${NODE_IMAGE} AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM ${NODE_IMAGE} AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000

RUN groupadd --system --gid 1001 nodejs \
  && useradd --system --uid 1001 --gid nodejs nextjs

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
