# code

代码目录，存放主框架模块。

## 当前应用结构

```text
code/
  frontend/          # Next.js 前端：浅色极简首页、SSE 展示、预览和复制链接
  backend/           # FastAPI 后端：LLM、数据库、OSS、权限、页面访问网关
  llm/               # 早期 TypeScript LLM 抽象参考实现
  nginx/             # Nginx 反向代理示例
  docker-compose.yml # MVP 双服务编排
```

## 后端

`code/backend/` 使用 FastAPI 实现主业务流程：

- `POST /api/generations` 创建页面生成任务，支持用户输入和上传文件一起生成页面。
- `GET /api/generations/{task_id}/events` 通过 SSE 推送生成过程。
- `GET /api/pages/{page_id}` 查询页面元数据。
- `GET /p/{page_id}` 作为页面访问网关，从私有 OSS 读取 HTML 并返回。

进入开发前需要先执行数据库迁移：

```bash
cd code/backend
python -m app.db.migrate
```

## 前端

`code/frontend/` 使用 Next.js 实现浅色极简首页，支持输入需求、上传 `docx/pptx/xlsx/xls/txt/md/html` 资料、查看 SSE 过程和页面预览。当前上传限制为 1 个文件、最大 50MB。开发时会将 `/api/*` 和 `/p/*` 转发到 FastAPI。

当前前端体验：

- 未开始生成时，首页保持 Gemini 风格的居中窄胶囊输入框。
- 开始生成后，切换为左侧 LLM 对话流、右侧页面预览的 1:1 双栏。
- 左侧展示用户需求、上传文件名和创建节点；文件较长时会先压缩为页面生成简报。模型思考作为一个默认展开的节点展示，可手动收起。
- 右侧用固定 `1200px` 桌面视口渲染生成页，再缩放到预览区域，尽量还原单独打开页面时的视觉效果。
- 复制链接支持 HTTP 环境下的降级复制，成功后按钮变绿并显示“复制成功”。
- 历史创建和当前会话暂存在浏览器 `localStorage`，刷新页面可恢复当前会话。

## Docker Compose

```bash
cd code
docker compose up --build
```

Compose 只将服务绑定在本机：

- Next.js：`127.0.0.1:3000`
- FastAPI：`127.0.0.1:8000`

公网仍只需要开放 `22/80/443`，由 Nginx 将 `/` 转发到前端，将 `/api/` 和 `/p/` 转发到后端。

当前上传资料最大 50MB，Nginx 示例配置中设置了 `client_max_body_size 60m`，避免 multipart 请求在进入 FastAPI 前被 Nginx 拦截。

## 当前运行方式

当前服务器上由 Nginx + systemd 常驻运行：

- Next.js：`127.0.0.1:3000`
- FastAPI：`127.0.0.1:8000`
- Nginx：`http://8.138.118.232/`

当前已使用 systemd 常驻运行：

```bash
systemctl status star-page-backend.service
systemctl status star-page-frontend.service
```

重启服务：

```bash
systemctl restart star-page-backend.service star-page-frontend.service
```

systemd 模板文件位于 `code/systemd/`，实际运行文件已复制到 `/etc/systemd/system/`。

## 后台队列升级提醒

当前 MVP 使用 SSE 长连接 + 数据库任务事件。出现以下情况时，应升级为后台队列 + Worker：

- 生成任务经常超过 1-2 分钟。
- 同时生成人数增加，FastAPI 长连接明显变多。
- 用户刷新页面后需要可靠恢复生成进度。
- 需要失败自动重试、任务排队、取消任务。
- 需要多台机器横向扩容生成能力。
