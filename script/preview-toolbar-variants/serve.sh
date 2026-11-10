#!/usr/bin/env bash
# 同一份前端代码起两个 dev 服务，对比「预览工具栏不遮挡页面」的两套方案（B 已淘汰）。
#   3011 -> 方案 A 浏览器顶栏
#   3013 -> 方案 C 胶片联动
# 地址后加 ?demo=9 进入演示（不调后端、不扣积分）；&live=1 让最后一个模型停在生成中。
# 用法：bash script/preview-toolbar-variants/serve.sh [start|stop|status]
set -euo pipefail

FRONTEND_DIR="$(cd "$(dirname "$0")/../../code/frontend" && pwd)"
BACKEND_URL="${BACKEND_INTERNAL_URL:-http://127.0.0.1:8000}"
VARIANTS=("A:3011" "C:3013")

stop() {
  for port in 3011 3012 3013; do
    pkill -f "next dev --webpack -H 0.0.0.0 -p ${port}" || true
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
      nohup npx next dev --webpack -H 0.0.0.0 -p "$port" > "/tmp/preview-toolbar-${lower}.log" 2>&1 &
    echo "方案 ${id}: http://localhost:${port}/?demo=9  日志 /tmp/preview-toolbar-${lower}.log"
  done
}

status() {
  ss -ltnp | awk 'NR==1 || /:301[13] /'
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "用法: $0 [start|stop|status]" && exit 1 ;;
esac
