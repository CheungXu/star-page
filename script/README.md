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

## Logo 透明化处理

`process_logo.py` 把 `image/stars-page-logo.png`（原始带白底）处理成透明背景 + 自动裁剪白边的版本，输出到：

- `image/stars-page-logo-transparent.png`：仓库内归档版本
- `code/frontend/public/stars-page-logo.png`：前端实际加载的版本（直接覆盖原文件）

处理步骤：

1. **清理近白色伪影**：阈值 235，把 R/G/B 三通道最小值都 ≥ 235 的像素一律置为纯白，消除原图边缘那条很浅的灰色伪影线（实测原图最右侧有一列 RGB≈228 的扫描伪影线，否则会撑大裁剪边界）。
2. **GIMP 风格 color-to-alpha**：参考色为纯白，按比例反推前景纯色 + alpha，确保抗锯齿边缘是带 alpha 的纯蓝色（视觉上无白雾、无锯齿）。
3. **自动裁剪**：依据 alpha 通道找出非透明像素的 bounding box（阈值 32 避免半透明伪影撑大边界），保留 16px 内边距。

使用：

```bash
pip install Pillow numpy --break-system-packages
python3 script/process_logo.py
```

执行后需要重新 `npm run build && systemctl restart star-page-frontend.service`，因为 systemd 服务用的是 `.next/standalone/public/` 中复制后的版本。

⚠️ 这是一次性的预处理脚本，源 PNG 一旦换新设计稿需要重新运行一次。如果将来获取到 SVG 矢量原图，建议直接用 SVG 替换 PNG，不再需要此脚本。
