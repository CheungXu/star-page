# syntax=docker/dockerfile:1

# 通用 Next.js/Node.js 生产镜像模板（保留作参考）。
# 实际前端镜像构建使用 code/frontend/Dockerfile，并通过 docker-compose（context: ./frontend）编排。
# 基础镜像默认指向阿里云 ACR 预存的 node 镜像；无 ACR 登录环境先询问用户登录，
# 用户选择不登录降级时再回退 Docker Hub：--build-arg NODE_IMAGE=node:22-bookworm-slim
ARG NODE_IMAGE=crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page/node:22-bookworm-slim

FROM ${NODE_IMAGE} AS deps
WORKDIR /app

COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./
RUN \
  if [ -f package-lock.json ]; then npm ci; \
  elif [ -f pnpm-lock.yaml ]; then corepack enable pnpm && pnpm install --frozen-lockfile; \
  elif [ -f yarn.lock ]; then corepack enable yarn && yarn install --frozen-lockfile; \
  else npm install; \
  fi

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

COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
