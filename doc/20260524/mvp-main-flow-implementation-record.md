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
- 前后端已改为 systemd 常驻运行：`star-page-backend.service` 和 `star-page-frontend.service`。
- 当前 LLM 配置为 `LLM_PROVIDER=qwen`、`LLM_PROTOCOL=openai`、`LLM_MODEL=qwen3.7-max`。
- 创建中阶段已调整为统一节点流：上传文件（如有）、解析文件、压缩内容、模型思考、模型输出答案、部署；模型思考节点默认展开并支持收起，模型输出节点优先展示模型返回的真实输出 token 数。
- 前端交互调整为两态布局：未开始生成时保持 Gemini 风格居中输入框；开始生成后切换为左侧 LLM 对话流、右侧页面预览的 1:1 双栏布局，生成过程统一收敛为节点流。
- 生成态进一步优化：长输入需求在左侧以带字数的可滚动气泡展示，页面预览 iframe 会根据生成页面内容自动选择宽屏、标准或长页高度；复制链接在 HTTP 环境下增加降级复制方案，并显示复制成功或失败反馈。
- 二次优化：收窄首页输入框宽度；生成态左侧内容区固定在视口内滚动，长输入和思考过程都限制最大高度；右侧预览卡固定在视口内，按生成页面比例使用 `wide`、`balanced`、`tall` 三种预览容器；复制链接只保留单一成功反馈，不再同时改变按钮文案。
- 三次优化：提交生成后清空底部输入框，只在对话流中保留用户需求；首页输入框改为更接近 Gemini 的窄胶囊比例；右侧预览改为固定 `1200px` 桌面视口渲染后整体缩放显示，避免生成页面因预览区宽度较窄触发移动端/单列响应式布局，尽量还原单独打开页面时的视觉效果。
- 上传资料优化：上传限制调整为单文件、最大 50MB；抽取文本超过 5000 字符时先由 LLM 压缩为面向页面生成的资料简报；生成任务会记录用户原始 prompt、文件名、抽取文本、压缩 prompt、最终入模 prompt 和 HTML OSS 调试定位信息；前端进度增加解析文件和压缩文件内容节点。

## 2026-05-25 上传与节点流优化

- 上传入口支持单文件资料输入，后端限制为 1 个文件、最大 50MB；Nginx 配置 `client_max_body_size 60m`，避免大文件在进入 FastAPI 前被拦截。
- 原始上传文件当前不持久保存；后端读取并抽取文本，Office 类文件通过 `MarkItDown` 临时落盘解析，临时文件解析后删除。
- 文件抽取文本超过 5000 字符时，先调用 LLM 压缩为面向页面生成任务的资料简报，再进入页面生成模型。
- `generation_tasks` 增加调试字段，记录用户原始 prompt、上传文件名、文件抽取文本、压缩 prompt、最终入模 prompt、生成 HTML 的 OSS 调试定位信息。
- 2026-05-26 追加 LLM 重试机制：底层客户端重试网络、超时、限流和 5xx 错误；资料压缩对空正文重试；页面生成在正式 HTML 输出开始前失败或空输出时重试。
- 前端生成过程统一为节点流：上传文件（如有）-> 解析文件 -> 压缩内容 -> 模型思考 -> 模型输出答案 -> 部署。
- `reasoning_content` 已并入“模型思考”节点，默认展开，支持手动收起；部署节点合并 HTML 上传 OSS 与数据库记录更新。

## 2026-05-26 预览、侧边栏和 LLM 可靠性修复

- 左侧历史区域改为贴边可折叠 tabbar，首页和生成页都展示；折叠态保留品牌、新对话、搜索和历史入口，展开后显示历史列表。
- 输入框优化方向调整为“压薄高度，不压窄宽度”：首页输入仍保持居中宽度，生成态底部输入框降低高度和阴影。
- 生成页预览继续使用固定 `1200px` 桌面视口宽度，但高度改为按预览区域反算，不再读取生成页整页 `scrollHeight`，避免生成页内 `100vh` 被超高 iframe 撑大导致首屏只露出大块背景。
- 排查到一次空白页问题：任务成功但 OSS 中 HTML 只有空壳 `<head>`、无 `<body>`。随后补充 `model_output_text` 调试字段，保存模型原始输出，便于区分模型输出问题和 HTML 提取/清洗问题。
- 排查到一次上传资料生成失败：请求在创建任务前的长文压缩阶段返回 `422`，原因是 LLM 压缩调用没有拿到正式 content。随后补充 LLM 重试机制，资料压缩对空正文重试，页面生成在正式输出开始前失败或空输出时重试。
- 再次确认 standalone 静态资源问题：`npm run build` 会重建 `.next/standalone`，可能移除 `.next/standalone/.next/static`。构建后必须重启 `star-page-frontend.service`，并检查 `/_next/static/*.css` 返回 `200 text/css`。

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

2026-05-26 再次复盘：在 systemd 服务运行中执行 `npm run build` 会重新生成 `.next/standalone`，可能把此前复制进去的 `.next/standalone/.next/static` 清掉。此时首页 HTML 仍可能返回 `200`，但 CSS 资源会返回 `500`，页面变成裸 HTML。以后每次构建后必须重启 `star-page-frontend.service`，并额外检查 `/_next/static/*.css` 返回 `200 text/css`。

### 生成页面预览

直接在右侧窄容器中渲染 iframe 会触发生成页面的响应式断点，导致单独打开时的桌面宫格布局在预览中变成移动端竖排。当前采用固定 `1200px` 桌面视口渲染 iframe，再整体缩放到预览区域的方式，尽量保持所见即所得。

### 当前仍需补齐

- 当前前后端已使用 systemd 常驻。后续如果切换为 Docker Compose，需要同步替换 systemd 服务或避免两套进程同时占用端口。
- 当前公网是 HTTP，复制链接已做降级复制；正式上线后应配置域名和 HTTPS。
- 生成任务仍是 SSE 长连接，后续达到升级条件时再拆后台队列和 Worker。

## 2026-05-24 晚间交互优化

- 复制链接按钮不再向上弹出成功提示，点击后按钮本身变为绿色并显示“复制成功”。
- 生成态增加左侧历史创建侧边栏，支持点击历史记录恢复会话。
- 增加“新对话”按钮；刷新页面会恢复当前会话，只有点击新对话才回到初始首页。
- 当前历史和会话状态保存在浏览器 `localStorage`，适合 demo；未来接入真实用户系统后，应改为后端页面列表和用户历史。
- 已补充 `wiki/systemd-nextjs-fastapi-deployment.md`，记录 Next.js + FastAPI 早期 MVP 使用 systemd 常驻运行的通用要点。

## 后台队列升级提醒

当前实现为 SSE 长连接 + 数据库任务事件。出现以下情况时，应升级为后台队列 + Worker：

- 生成任务经常超过 1-2 分钟。
- 同时生成人数增加，FastAPI 长连接明显变多。
- 用户刷新页面后需要可靠恢复生成进度。
- 需要失败自动重试、任务排队、取消任务。
- 需要多台机器横向扩容生成能力。
