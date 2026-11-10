#!/usr/bin/env bash
# 同一份前端代码起多个 dev 服务，对比「预览工具栏不遮挡页面」的方案（B 已淘汰，C 拆成两版）。
#   3011 -> 方案 A  浏览器顶栏
#   3013 -> 方案 C1 胶片联动 · 精修（保留用量信息）
#   3014 -> 方案 C2 胶片联动 · 极简（竖排菜单）
# C1 / C2 地址加 &cont=pill|bar|corner 切换缩略图上「继续改」的样式。
# 地址后加 ?demo=9 进入演示（不调后端、不扣积分）；&live=1 让最后一个模型停在生成中。
# 用法：bash script/preview-toolbar-variants/serve.sh [start [A,C1,C2]|stop|status]
set -euo pipefail

FRONTEND_DIR="$(cd "$(dirname "$0")/../../code/frontend" && pwd)"
BACKEND_URL="${BACKEND_INTERNAL_URL:-http://127.0.0.1:8000}"
VARIANTS=("A:3011" "C1:3013" "C2:3014")

stop() {
  for port in "$@"; do
    pkill -f "next dev --webpack -H 0.0.0.0 -p ${port}" || true
  done
}

start() {
  local only="${1:-}"
  cd "$FRONTEND_DIR"
  for item in "${VARIANTS[@]}"; do
    id="${item%%:*}"
    [[ -n "$only" && ",$only," != *",$id,"* ]] && continue
    port="${item#*:}"
    stop "$port"
    lower="$(echo "$id" | tr 'A-Z' 'a-z')"
    NEXT_PUBLIC_UX_VARIANT="$id" NEXT_DIST_DIR=".next-${lower}" BACKEND_INTERNAL_URL="$BACKEND_URL" \
      nohup npx next dev --webpack -H 0.0.0.0 -p "$port" > "/tmp/preview-toolbar-${lower}.log" 2>&1 &
    echo "方案 ${id}: http://localhost:${port}/?demo=9  日志 /tmp/preview-toolbar-${lower}.log"
  done
}

status() {
  ss -ltnp | awk 'NR==1 || /:301[134] /'
}

case "${1:-start}" in
  start) start "${2:-}" ;;
  stop) stop 3011 3012 3013 3014 ;;
  status) status ;;
  *) echo "用法: $0 [start [A,C1,C2]|stop|status]" && exit 1 ;;
esac
