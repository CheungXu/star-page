#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-config/db.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "未找到数据库配置文件：$ENV_FILE"
  echo "请先复制 config/db.env.example 为 $ENV_FILE，并填写 RDS 连接信息。"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-10}"

if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "使用 DATABASE_URL 测试 PostgreSQL 连接（不会输出密码）。"
  pg_isready -d "$DATABASE_URL"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "select current_database() as database, current_user as user;"
else
  missing=()
  [[ -z "${PGHOST:-}" ]] && missing+=("PGHOST")
  [[ -z "${PGPORT:-}" ]] && missing+=("PGPORT")
  [[ -z "${PGDATABASE:-}" ]] && missing+=("PGDATABASE")
  [[ -z "${PGUSER:-}" ]] && missing+=("PGUSER")
  [[ -z "${PGPASSWORD:-}" ]] && missing+=("PGPASSWORD")

  if (( ${#missing[@]} > 0 )); then
    echo "数据库配置缺少字段：${missing[*]}"
    exit 1
  fi

  echo "测试 PostgreSQL 连接：${PGUSER}@${PGHOST}:${PGPORT}/${PGDATABASE}"
  pg_isready -h "$PGHOST" -p "$PGPORT" -d "$PGDATABASE" -U "$PGUSER"
  psql -h "$PGHOST" -p "$PGPORT" -d "$PGDATABASE" -U "$PGUSER" -v ON_ERROR_STOP=1 -c "select current_database() as database, current_user as user;"
fi
