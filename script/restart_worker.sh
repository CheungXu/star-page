#!/usr/bin/env bash
# 逐个重启生成 worker（star-page-worker@<编号>），生成不中断。不会重启 web。
#
# 每个 worker 收到停止信号后先排空：不领新任务，等手上的跑完（最多 6 分钟）再退出；
# 这期间其他 worker 照常认领新任务。一个起来了再重启下一个，所以任何时候至少有一个在领任务。
# 只有一个 worker 时，排空期间新任务会排队，等它重启完再开始。
#
# 用法：script/restart_worker.sh [编号 ...]，默认重启所有正在运行的 star-page-worker@* 实例。
set -euo pipefail

IDS=("$@")
if [ ${#IDS[@]} -eq 0 ]; then
  mapfile -t IDS < <(systemctl list-units 'star-page-worker@*.service' --state=active --no-legend --plain \
    | awk '{print $1}' | sed -n 's/^star-page-worker@\(.\+\)\.service$/\1/p' | sort)
fi
if [ ${#IDS[@]} -eq 0 ]; then
  echo "没有正在运行的 star-page-worker@* 实例" >&2
  exit 1
fi

echo "将依次重启：${IDS[*]}"
for id in "${IDS[@]}"; do
  unit="star-page-worker@${id}.service"
  since=$(date '+%Y-%m-%d %H:%M:%S')
  echo "重启 ${unit}（先排空，手上有任务时可能要等几分钟）"
  systemctl restart "${unit}"
  started=""
  for _ in $(seq 1 60); do
    if journalctl -u "${unit}" --since "${since}" --no-pager -o cat | grep -q '^\[worker\] 已启动'; then
      started=1
      break
    fi
    sleep 0.5
  done
  if [ -z "${started}" ]; then
    echo "${unit} 30 秒内没有启动完成，停止滚动重启，请看 journalctl -u ${unit}" >&2
    exit 1
  fi
  journalctl -u "${unit}" --since "${since}" --no-pager -o cat | grep -E '^\[worker\] (已排空|排空超时)' || true
  echo "${unit} 已启动"
done
echo "全部 worker 已重启"
