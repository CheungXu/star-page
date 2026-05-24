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

## RDS 业务库授权准备

`prepare_rds_database.sql` 用于在应用账号缺少建表权限时，由高权限数据库账号先创建业务库并授予应用账号建表权限。

当前 MVP 后端迁移需要应用账号能在业务数据库的 `public` schema 下创建表：

```sql
GRANT USAGE, CREATE ON SCHEMA public TO stars_page_demo;
```

建议业务表放在独立数据库 `stars_page`，不要长期放在默认 `postgres` 数据库中。
