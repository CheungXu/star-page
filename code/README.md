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
- `GET /api/generations/batches/{batch_id}/events` 一轮一条 SSE 连接，带这一轮各模型的事件（payload 带 `task_id`），支持断线按游标续传；生成由后端后台执行器跑完，连接断开不影响生成（单任务的 `/{task_id}/events` 保留只读兼容）。
- `POST /api/generations/batches/{batch_id}/cancel` 停止这一轮还没完成的模型，已完成的保留并计费。被停的记为 `cancelled`：输出未满 2,000 Token（含思考）或系统报错时解冻退回；主动停止且已超过该线时按 1.0 倍原始成本保底收费。界面只显示「已停止」，收费规则写在充值协议里。
- `GET /api/models` 返回模型目录（key/label/是否默认/是否可用，以及档位、预留积分、未充值是否需首充），供前端动态渲染多选。
- `GET /api/conversations`、`GET /api/conversations/{id}` 会话列表（按会话一条）与会话树（批次 + 各模型节点，含 `usage`、`billing`、`duration_ms`），用于历史与多轮恢复。
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

`code/frontend/` 使用 Next.js 实现浅色极简首页，支持输入需求、上传 `docx/pptx/xlsx/xls/pdf/txt/md/html` 资料、查看 SSE 过程和页面预览。当前上传限制为最多 3 个文件、单文件与总大小最大 50MB。开发时会将 `/api/*` 和 `/p/*` 转发到 FastAPI。

当前前端体验：

- 未开始生成时，首页展示贴边可折叠 tabbar 和 Gemini 风格的居中输入框；输入卡下方可多选并行模型（默认勾选项来自 `GET /api/models` 的 `is_default`，当前为 `qwen` + `doubao`；用户曾选过的模型会保存在 `localStorage` 的 `star-page-selected-models`）。
- 开始生成后，主区域切换为左侧对话栏、右侧结果预览区，两栏之间可通过拖拽分隔条调整宽度（记忆在 `localStorage`）。工作区组件集中在 `code/frontend/app/components/workspace/`。
- 左侧对话栏按轮次排列，每轮由「带来源标识（新建 / 基于某结果 / 各自续改）的用户需求气泡 + 左对齐的进度回复气泡」组成。进度回复气泡收起时展示完成数、用时、tokens 与积分，展开后为轻量模型明细表（阶段、输出比例条、用时、积分、最快/最省/→第N轮标签）；点击历史轮次的回复气泡即可在右侧回看该轮结果。
- 右侧结果区：1-2 个模型并排对比；3 个以上采用「大舞台 + 底部胶片缩略条 + 全屏轮播」（支持键盘左右键切换）。每个结果是一个"浏览器视窗"单元（固定 `1200px` 视口按单元宽度缩放），支持独立打开/复制链接、发布固定链接、以及"以此结果继续"续写；高度按单元反算，避免生成页 `100vh` 被整页高度撑大。
- 每轮一条 SSE 订阅全轮事件，先完成的先展示；名额已满时超出的模型显示「排队中」，生成中发送按钮变成「停止生成」；历史与多轮会话恢复基于 `GET /api/conversations/{id}`。
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

公网仍只需要开放 `22/80/443`，由 Nginx 将 `/` 转发到前端，将 `/api/`、`/p/`、`/u/`、`/assets/` 转发到后端。

当前上传资料最大 50MB，Nginx 示例配置中设置了 `client_max_body_size 60m`，避免 multipart 请求在进入 FastAPI 前被 Nginx 拦截。

## 当前运行方式

当前服务器上由 Nginx + systemd 常驻运行：

- Next.js：`127.0.0.1:3000`
- FastAPI web：`127.0.0.1:8000`、`127.0.0.1:8001`（两个实例，Nginx 在两者之间转发）
- 生成 worker：不对外，只连数据库、对象存储和模型服务
- Nginx：对外 `80/443`；本机 `127.0.0.1:8080` 供 Next.js 服务端取数

```bash
systemctl status star-page-worker.service 'star-page-web@*'
systemctl status star-page-frontend.service
systemctl status star-page-analytics.timer   # 运营指标聚合（每小时 :05 + 每日 00:10）
```

重启服务：

```bash
script/restart_web.sh                        # 改了接口、分享页等：逐个重启两个 web，不中断服务
systemctl restart star-page-worker.service   # 改了生成相关代码：在跑的生成会由启动扫尾重跑
# 前端改代码后须先 npm run build，再 restart frontend（见 code/systemd/README.md）
```

运营指标聚合定时器安装见 `code/systemd/README.md`；手动补跑：`python -m app.analytics.aggregate --backfill 90`。

systemd 模板文件位于 `code/systemd/`，实际运行文件已复制到 `/etc/systemd/system/`。

## 后台执行与后续扩容

生成任务由单独的 worker 进程跑完（先过每用户上限 10、再过全站上限 48，重启后自动扫尾重跑近期中断的任务）；web 进程只接请求，用 PostgreSQL `LISTEN/NOTIFY` 通知 worker 派发和停止，worker 再通知 web 唤醒事件流。详见 `code/backend/README.md` 的「后台生成」和 `wiki/sse-background-job-streaming.md`。名额和任务登记表在 worker 的内存里，所以 worker 只能开一个；以后要多台机器跑生成，见 `alternatives/backend-process-model/README.md`。
