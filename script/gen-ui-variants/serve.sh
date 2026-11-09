#!/usr/bin/env bash
# 同一份前端代码起三个 dev 服务，对比生成进度 / 多模型结果的三套方案。
#   3011 -> 方案 A 轻量看板
#   3012 -> 方案 B 沉浸画廊
#   3013 -> 方案 C 赛道对比
# 用法：bash script/gen-ui-variants/serve.sh [start|stop]
set -euo pipefail

FRONTEND_DIR="$(cd "$(dirname "$0")/../../code/frontend" && pwd)"
BACKEND_URL="${BACKEND_INTERNAL_URL:-http://127.0.0.1:8000}"
VARIANTS=("A:3011" "B:3012" "C:3013")

stop() {
  for item in "${VARIANTS[@]}"; do
    port="${item#*:}"
    pkill -f "next dev -H 0.0.0.0 -p ${port}" || true
  done
}

start() {
  stop
  cd "$FRONTEND_DIR"
  for item in "${VARIANTS[@]}"; do
    id="${item%%:*}"
    port="${item#*:}"
    lower="$(echo "$id" | tr 'A-Z' 'a-z')"
    NEXT_PUBLIC_UX_VARIANT="$id" NEXT_DIST_DIR=".next-${lower}" BACKEND_INTERNAL_URL="$BACKEND_URL" \
      nohup npx next dev -H 0.0.0.0 -p "$port" > "/tmp/gen-ui-${lower}.log" 2>&1 &
    echo "方案 ${id}: http://127.0.0.1:${port}/?demo=7  日志 /tmp/gen-ui-${lower}.log"
  done
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  *) echo "用法: $0 [start|stop]" && exit 1 ;;
esac
