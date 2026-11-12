#!/usr/bin/env bash
# 逐个重启后端 web 实例（star-page-web@<端口>），不中断对外服务。不会重启生成 worker。
#
# 顺序：重启一个 → 等它健康检查通过 → 再等过 Nginx 的 fail_timeout（5 秒）→ 才动下一个。
# 不等这 5 秒，Nginx 仍把刚恢复的实例当作不可用，下一个一停就可能所有实例同时不可用。
#
# 用法：script/restart_web.sh [端口 ...]，默认重启所有正在运行的 star-page-web@* 实例。
set -euo pipefail

PORTS=("$@")
if [ ${#PORTS[@]} -eq 0 ]; then
  mapfile -t PORTS < <(systemctl list-units 'star-page-web@*.service' --state=active --no-legend --plain \
    | awk '{print $1}' | sed -n 's/^star-page-web@\([0-9]\+\)\.service$/\1/p' | sort -n)
fi
if [ ${#PORTS[@]} -eq 0 ]; then
  echo "没有正在运行的 star-page-web@* 实例" >&2
  exit 1
fi
if [ ${#PORTS[@]} -eq 1 ] && [ $# -eq 0 ]; then
  echo "只有一个 web 实例在跑（${PORTS[0]}），重启期间会短暂不可用" >&2
fi
NGINX_FAIL_TIMEOUT=5

echo "将依次重启：${PORTS[*]}"
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
