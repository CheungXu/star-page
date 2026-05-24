# MVP 主流程实现记录

## 本次实现范围

已按“Next.js 前端 + FastAPI 后端”实现 MVP 主流程骨架：

- `code/frontend/`：浅色极简首页、SSE 思考过程展示、页面创建中状态、iframe 预览、一键复制链接。
- `code/backend/`：FastAPI API、默认测试用户、数据库迁移、LLM 流式客户端、OSS StorageProvider、HTML 清洗、页面访问网关。
- `code/docker-compose.yml`：前后端双服务编排，端口仅绑定本机。
- `code/nginx/star-page.conf.example`：Nginx 反向代理示例。
- `script/prepare_rds_database.sql`：RDS 应用账号缺少建表权限时的高权限账号准备脚本。

## 当前验证结果

- 后端 Python 语法检查通过。
- FastAPI 应用可正常导入。
- `/healthz` 健康检查可返回 `200 {"status":"ok"}`。
- HTML 清洗逻辑已验证可移除 `<script>`、事件属性和 `javascript:` 链接。
- 前端依赖安装成功。
- 使用临时 Node 22 执行 `next build` 成功。
- RDS 业务库已统一使用 `stars_page`，迁移已成功执行，`default_test` 默认用户已初始化。
- 当前临时访问入口为 `http://8.138.118.232/`，Nginx 将 `/` 转发到 Next.js，将 `/api/` 和 `/p/` 转发到 FastAPI。
- 当前 LLM 配置为 `LLM_PROVIDER=qwen`、`LLM_PROTOCOL=openai`、`LLM_MODEL=qwen3.7-max`。
- 创建中阶段已增加进度节点：模型输出答案、上传文件中、记录数据库；模型输出节点优先展示模型返回的真实输出 token 数。
- 前端交互调整为两态布局：未开始生成时保持 Gemini 风格居中输入框；开始生成后切换为左侧 LLM 对话流、右侧页面预览的 1:1 双栏布局，创建节点展示在思考过程下方。
- 生成态进一步优化：长输入需求在左侧以带字数的可滚动气泡展示，页面预览 iframe 会根据生成页面内容自动选择宽屏、标准或长页高度；复制链接在 HTTP 环境下增加降级复制方案，并显示复制成功或失败反馈。
- 二次优化：收窄首页输入框宽度；生成态左侧内容区固定在视口内滚动，长输入和思考过程都限制最大高度；右侧预览卡固定在视口内，按生成页面比例使用 `wide`、`balanced`、`tall` 三种预览容器；复制链接只保留单一成功反馈，不再同时改变按钮文案。
- 三次优化：提交生成后清空底部输入框，只在对话流中保留用户需求；首页输入框改为更接近 Gemini 的窄胶囊比例；右侧预览改为固定 `1200px` 桌面视口渲染后整体缩放显示，避免生成页面因预览区宽度较窄触发移动端/单列响应式布局，尽量还原单独打开页面时的视觉效果。

## 已解决问题

### RDS 建表权限

曾出现 RDS 应用账号可以连接数据库、但没有在 `public` schema 创建表权限的问题：

```text
permission denied for schema public
```

最终处理方式：

1. 在 RDS 控制台创建业务数据库 `stars_page`。
2. 将真实 `config/db.env` 的 `PGDATABASE` 改为 `stars_page`。
3. 重新执行迁移：

```bash
cd code/backend
.venv/bin/python -m app.db.migrate
```

迁移成功后已创建 6 张业务表，并初始化 `default_test` 用户。

### Next.js standalone 静态资源

曾出现页面退化成裸 HTML 的问题，原因是 standalone 运行目录缺少 `.next/static`。每次重新构建并用 standalone 方式临时启动时，需要执行：

```bash
cd code/frontend
npx -p node@22 node node_modules/next/dist/bin/next build
mkdir -p .next/standalone/.next
rm -rf .next/standalone/.next/static .next/standalone/public
cp -r .next/static .next/standalone/.next/
cp -r public .next/standalone/
```

### 生成页面预览

直接在右侧窄容器中渲染 iframe 会触发生成页面的响应式断点，导致单独打开时的桌面宫格布局在预览中变成移动端竖排。当前采用固定 `1200px` 桌面视口渲染 iframe，再整体缩放到预览区域的方式，尽量保持所见即所得。

### 当前仍需补齐

- 当前前后端是临时后台进程，服务器重启后不会自动恢复。后续应切换到 Docker Compose 或 systemd 常驻。
- 当前公网是 HTTP，复制链接已做降级复制；正式上线后应配置域名和 HTTPS。
- 生成任务仍是 SSE 长连接，后续达到升级条件时再拆后台队列和 Worker。

## 后台队列升级提醒

当前实现为 SSE 长连接 + 数据库任务事件。出现以下情况时，应升级为后台队列 + Worker：

- 生成任务经常超过 1-2 分钟。
- 同时生成人数增加，FastAPI 长连接明显变多。
- 用户刷新页面后需要可靠恢复生成进度。
- 需要失败自动重试、任务排队、取消任务。
- 需要多台机器横向扩容生成能力。
