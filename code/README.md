# code

代码目录，存放主框架模块。

## 当前应用结构

```text
code/
  frontend/          # Next.js 前端：浅色极简首页、SSE 展示、预览和复制链接
  backend/           # FastAPI 后端：LLM、数据库、OSS、权限、页面访问网关、运营埋点与聚合
  llm/               # 早期 TypeScript LLM 抽象参考实现
  nginx/             # Nginx 反向代理示例
  docker-compose.yml # MVP 双服务编排
```

## 后端

`code/backend/` 使用 FastAPI 实现主业务流程：

- `POST /api/generations` 创建一轮生成（批次）：支持 `models` 多选并行、上传文件、以及 `conversation_id`+`base_page_id` 续写；每个模型一个独立 `Page` 节点，返回 `conversation_id/batch_id/runs[]`。
- `GET /api/generations/{task_id}/events` 通过 SSE 推送单个模型 run 的生成过程（前端为每个 run 开一路）。
- `GET /api/models` 返回模型目录（key/label/是否默认/是否可用），供前端动态渲染多选。
- `GET /api/conversations`、`GET /api/conversations/{id}` 会话列表（按会话一条）与会话树（批次 + 各模型节点），用于历史与恢复。
- `GET /api/pages/{page_id}` 查询页面元数据。
- `GET /p/{conversation_id}/{page_id}` 作为页面访问网关，校验节点归属会话后从私有 OSS 读取 HTML 并返回。生成页支持展示型 CSS/JS，网关下发"隔离优先"的沙箱 CSP（`sandbox allow-scripts ...; connect-src 'none'`），详见 `wiki/generated-page-js-sandbox-and-security.md`。
- **运营后台**：`/ops` 前端 + `/api/admin/analytics/*` 管理端接口；明细埋点（`page_view_events` / `analytics_events`）+ 离线聚合（`metric_daily` 等）+ systemd 定时器。详见 `code/backend/app/analytics/README.md`、`doc/20260628/ops-backend-design-and-implementation.md`。

多模型生成采用"会话(生成树) → 批次(一轮) → 节点(每模型一个独立可分享 Page)"结构，详见 `wiki/multi-model-generation-tree.md`；模型走"可提交模型目录 `config/llm.models.json` + 仅密钥 env + 参数三层覆盖"，详见 `wiki/llm-provider-abstraction.md`。

进入开发前需要先执行数据库迁移：

```bash
cd code/backend
python -m app.db.migrate
```

## 前端

`code/frontend/` 使用 Next.js 实现浅色极简首页，支持输入需求、上传 `docx/pptx/xlsx/xls/txt/md/html` 资料、查看 SSE 过程和页面预览。当前上传限制为 1 个文件、最大 50MB。开发时会将 `/api/*` 和 `/p/*` 转发到 FastAPI。

当前前端体验：

- 未开始生成时，首页展示贴边可折叠 tabbar 和 Gemini 风格的居中输入框；输入卡下方可多选并行模型（默认勾选项来自 `GET /api/models` 的 `is_default`，当前为 `qwen` + `doubao`；用户曾选过的模型会保存在 `localStorage` 的 `star-page-selected-models`）。
- 开始生成后，主区域切换为左侧 LLM 对话流、右侧页面预览；单模型为 1:1 双栏，多模型对比时会话栏收窄、预览栏加宽。
- 左侧展示用户需求、上传文件名和创建节点；多模型时用"本轮模型" tab 切换查看各模型的思考与创建节点。模型思考默认展开、可手动收起。
- 右侧并排对比各模型结果，每个结果是一个"浏览器视窗"单元（固定 `1200px` 视口按单元宽度缩放），支持单元"聚焦"放大、独立打开/复制链接、以及"以此结果继续"续写；高度按单元反算，避免生成页 `100vh` 被整页高度撑大。
- 每个模型 run 单独一路 SSE，先完成的先展示；历史与会话恢复基于 `GET /api/conversations`。
- 复制链接支持 HTTP 环境下的降级复制，成功后按钮变绿并显示“复制成功”。
- 历史创建从后端数据库读取，当前按手机号登录用户隔离；浏览器 `localStorage` 仅保留当前设备的会话细节。

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
systemctl status star-page-analytics.timer   # 运营指标聚合（每小时 :05 + 每日 00:10）
```

重启服务：

```bash
systemctl restart star-page-backend.service star-page-frontend.service
# 前端改代码后须先 npm run build，再 restart frontend（见 code/systemd/README.md）
```

运营指标聚合定时器安装见 `code/systemd/README.md`；手动补跑：`python -m app.analytics.aggregate --backfill 90`。

systemd 模板文件位于 `code/systemd/`，实际运行文件已复制到 `/etc/systemd/system/`。

## 后台队列升级提醒

当前 MVP 使用 SSE 长连接 + 数据库任务事件。出现以下情况时，应升级为后台队列 + Worker：

- 生成任务经常超过 1-2 分钟。
- 同时生成人数增加，FastAPI 长连接明显变多。
- 用户刷新页面后需要可靠恢复生成进度。
- 需要失败自动重试、任务排队、取消任务。
- 需要多台机器横向扩容生成能力。
