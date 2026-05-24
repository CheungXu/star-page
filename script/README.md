# script

脚本目录，存放临时支持任务的脚本。

## 数据库连接测试

`check_postgres_connection.sh` 用于从当前服务器验证 PostgreSQL/RDS 连接。

使用前准备：

1. 复制 `config/db.env.example` 为 `config/db.env`。
2. 在 `config/db.env` 中填写真实 RDS 连接信息。
3. 确认 `config/db.env` 已被 `config/.gitignore` 忽略。

执行：

```bash
bash script/check_postgres_connection.sh
```

脚本会优先使用 `DATABASE_URL`。如果没有填写 `DATABASE_URL`，则使用 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`、`PGPASSWORD`。
