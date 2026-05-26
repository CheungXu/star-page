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

### 完整版（带扫描伪影清理）：`process_logo.py`

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

### 轻量版（纯白底无伪影）：`preview-logo/process_simple_logo.py`

针对**白底干净、无扫描伪影**的源图，约 30 行代码完成"透明化 + 自动裁剪"，无需 color-to-alpha：

- 单参数亮度阈值法：lum ≥ 245 → alpha 0；lum ≤ 220 → alpha 255；中间线性过渡。
- 基于 alpha 通道 `getbbox()` 自动裁剪 + 16px padding。

输出 `script/preview-logo/stars-page-logo-simple.png`，作为生产首页中央 Hero logo（54px）+ 侧边栏底板内 logo（26px）使用。两个版本互不冲突。

## 多端口 Logo 方案对比预览：`preview-logo/`

`preview-logo/` 目录是一套独立的"前端方案 A/B/C 对比预览"基础设施：

| 文件 | 用途 |
|---|---|
| `preview-a.html` | 方案一：简化并缩小（44px / 56px 单星） |
| `preview-b.html` | 方案二：中央不放 Logo |
| `preview-c.html` | 方案三：原 Logo 转化为氛围水印 |
| `_styles.css` | 三方案公用的精简样式（从生产 globals.css 抽取首屏所需片段） |
| `serve.py` | 多端口 Python `http.server`，同时绑定 3001 / 3002 / 3003 |
| `process_simple_logo.py` | 简化版 logo 透明化脚本 |
| `stars-page-logo-simple.png` | 处理后的简化版 logo |

启动：

```bash
cd script/preview-logo
python3 serve.py        # 同时占用 3001/3002/3003
# 浏览器分别打开 http://localhost:3001/ /3002/ /3003/ 对比

# 后台常驻
nohup python3 serve.py > /tmp/preview-logo.log 2>&1 &

# 停止
pkill -f preview-logo/serve.py
```

⚠️ 仅作视觉对比用，无后端调用、无真实交互。方案选定后需要把"通用优化"（padding / 阴影 / 字号等非方案差异项）双向同步到生产 `globals.css` 与本目录的 `_styles.css`。

跨项目可复用方法见 `wiki/multi-port-static-preview-for-design-variants.md`。
