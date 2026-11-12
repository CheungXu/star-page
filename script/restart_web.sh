#!/usr/bin/env bash
# 逐个重启后端 web 实例（star-page-web@8000 / @8001），不中断对外服务。不会重启生成 worker。
#
# 顺序：重启一个 → 等它健康检查通过 → 再等过 Nginx 的 fail_timeout（5 秒）→ 才动下一个。
# 不等这 5 秒，Nginx 仍把刚恢复的实例当作不可用，第二个一停就会两边同时不可用。
#
# 用法：script/restart_web.sh [端口 ...]，默认 8000 8001。
set -euo pipefail

PORTS=("$@")
if [ ${#PORTS[@]} -eq 0 ]; then
  PORTS=(8000 8001)
fi
NGINX_FAIL_TIMEOUT=5

for port in "${PORTS[@]}"; do
  echo "重启 star-page-web@${port}"
  systemctl restart "star-page-web@${port}.service"
  for _ in $(seq 1 60); do
    if [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port}/healthz" || true)" = "200" ]; then
      break
    fi
    sleep 0.5
  done
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port}/healthz" || true)" != "200" ]; then
    echo "star-page-web@${port} 30 秒内没有通过健康检查，停止滚动重启，请看 journalctl -u star-page-web@${port}" >&2
    exit 1
  fi
  echo "star-page-web@${port} 已就绪，等 ${NGINX_FAIL_TIMEOUT} 秒让 Nginx 重新把它算作可用"
  sleep $((NGINX_FAIL_TIMEOUT + 1))
done
echo "全部 web 实例已重启"
