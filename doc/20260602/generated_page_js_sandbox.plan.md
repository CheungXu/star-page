---
name: generated page js sandbox
overview: 在不推翻现有生成树/SSE/多模型主干的前提下，安全地为单文件展示页放开 CSS/JS：用 CSP sandbox + sandboxed iframe 把会跑 JS 的展示内容关进"无主站凭证、无外部网络"的密封盒子，并为后续多文件/agentic 阶段留好接缝。
todos:
  - id: prompt
    content: 重写 HTML_PAGE_SYSTEM_PROMPT：允许展示型内联 JS/CSS、单自包含文件，禁止对外网络/真实表单提交/plugin/外部 iframe
    status: completed
  - id: sanitizer
    content: html_sanitizer 直接改为 interactive 行为：放行 script/事件/form/canvas/svg，移除 object/embed/base/iframe/meta-refresh，按 CDN 白名单过滤外部 script src（不保留双档）
    status: completed
  - id: csp-config
    content: config 增加 GENERATED_PAGE_CDN_ALLOWLIST；/p 网关统一输出 sandbox + connect-src none 的 interactive CSP（不按 render_mode 分支）
    status: completed
  - id: frontend-iframe
    content: 预览 iframe 增加 sandbox=allow-scripts 等属性，确认缩放逻辑不受影响
    status: completed
  - id: url-tree
    content: 页面访问网关改为 /p/{conversation_id}/{page_id} 层级路径：新增统一 build_page_url 助手，更新 generation_service/routes_conversations/routes_pages 全部 URL 构造与 serve_page 路由+一致性校验；前端无需改（仅消费服务端 page_url）
    status: completed
  - id: delete-cascade
    content: 删除会话时级联软删其下 Page 节点（delete_conversation），网关回看会话已删则 404，修复"删会话后页面仍可访问"
    status: completed
  - id: docs
    content: 沉淀 wiki 安全知识条 + doc/20260602 实施记录，并把本 plan 归档到 doc/20260602；更新 backend/code README 中 /p 路径与关于 JS 的描述、整体架构与原架构规划，记录多文件/agentic roadmap
    status: in_progress
isProject: false
---

# 为展示页安全放开 CSS/JS（沙箱化单文件）

## 背景与定位

产品是"替代 Word/PPT 的 AI 富展示页"，JS/CSS 仅为让展示更美观、交互更丰富，不做成真正应用。因此可主动加限制。安全范式从"清洗掉所有 JS"转为"**隔离优先**"：用浏览器原生沙箱把会跑 JS 的内容关进不透明 origin，使其碰不到主站凭证、也碰不到外部网络。

已确认的四个决策：
- 运行边界：纯静态优先（OSS+CDN，不跑服务端）。
- 生成演进：本轮仍单文件、只放开 CSS/JS；下一步一次性多文件；最后到 agentic 工作区。
- 隔离机制：CSP `sandbox` + sandboxed iframe（无需新域名）。
- 外部网络：默认禁网（`connect-src 'none'`）+ 可信 CDN 白名单满足美观；未来按页 opt-in 受控放开。

> 预研阶段说明：当前为预研，不背历史包袱。**不为老数据保留双档**——直接把所有生成页统一升级为 interactive（老页本就无 JS，用更宽的 interactive CSP 渲染无害），因此**不引入 `render_mode` 字段、不加数据库迁移**。本 plan 同步归档一份到 `doc/20260602/`，便于回溯。

## 安全模型（本轮核心）

两道独立的墙叠加：
- `sandbox`（不含 `allow-same-origin`）→ 页面进入不透明 origin，读不到主站 Cookie/localStorage（关掉"偷主站登录态"）。
- `connect-src 'none'` + `form-action 'none'` → 关掉"钓鱼/信标/把平台域名当分发渠道"（sandbox 管不到 fetch，必须靠 CSP）。
- prompt/模型控制 + 后续内容审核作为**附加层**（合规/质量），不作为主墙。

直接打开分享链接是顶层文档（无父 iframe），因此沙箱必须由 **响应头 `Content-Security-Policy: sandbox ...`** 施加；预览场景再叠加 iframe 的 `sandbox` 属性。

## 本轮具体改动

### 1. 生成 Prompt — [prompt.py](code/backend/app/services/llm/prompt.py)
- 重写 `HTML_PAGE_SYSTEM_PROMPT`：允许内联 JS 用于展示交互（动画、tab、轮播、图表、折叠、滚动效果）；仍输出**单个自包含 HTML 文件**。
- 明确禁止：任何对外网络请求（fetch/XHR/WebSocket）、会真正提交的表单、`<object>/<embed>/<base>`、外部 `<iframe>`；数据须写死在页面内。
- 允许：内联 `<script>/<style>`、`<canvas>`、SVG；如需库优先自包含，必要时仅用白名单 CDN。

### 2. HTML 清洗 — [html_sanitizer.py](code/backend/app/services/html_sanitizer.py)
- 直接把 `sanitize_html` 改为 interactive 行为（不保留双档）：
  - 放行：`<script>`（内联 + 白名单 CDN host 的 src）、`on*` 事件属性、`<form>/<input>`（提交由 CSP `form-action 'none'` 拦截）、canvas、svg。
  - 仍移除：`<object>/<embed>/<base>/<iframe>`、`meta http-equiv=refresh`；丢弃非白名单的外部 `<script src>`。

### 3. 网关 CSP 与配置 — [routes_pages.py](code/backend/app/api/routes_pages.py)、[config.py](code/backend/app/core/config.py)
- 新增配置 `GENERATED_PAGE_CDN_ALLOWLIST`（默认 `https://cdn.jsdelivr.net https://unpkg.com`），供清洗与 CSP 共用。
- `/p/{page_id}` 统一输出同一条 interactive CSP（不分支）：
  - `sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox; default-src 'none'; script-src 'unsafe-inline' <CDN白名单>; style-src 'unsafe-inline' <CDN白名单>; img-src https: data:; font-src https: data:; media-src https: data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'`。
- 说明：因 sandbox 进入不透明 origin，`'self'` 不再匹配，故脚本/样式用 `'unsafe-inline'` + 显式 CDN host；`frame-ancestors 'self'` 供主站预览嵌入。老页面无 JS，用此 CSP 渲染无害。

### 4. 前端预览 iframe — [page.tsx](code/frontend/app/page.tsx)（约 605 行）
- 给预览 `<iframe>` 加 `sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"`。
- 无需改动缩放逻辑：`recomputeMetrics()` 用容器尺寸反算，不读 `contentDocument`，沙箱跨域不影响。

### 5. 页面访问 URL 改为树层级 + 删会话级联

要点：把扁平的 `/p/{page_id}` 升级为体现"会话→节点"归属的 `/p/{conversation_id}/{page_id}`，并修复"删会话后节点链接仍可访问"。预研阶段不保留旧扁平链接。

- URL 层级化：
  - 新增统一助手 `build_page_url(settings, page)` → `{public_base_url}/p/{page.conversation_id}/{page.id}`（放 `app/core/` 中性模块，避免 services 依赖 routes）。
  - 替换全部构造点：[generation_service.py](code/backend/app/services/generation_service.py) `_page_url`（第 197 行，含 `BatchRunRef` 与 completed 事件）、[routes_conversations.py](code/backend/app/api/routes_conversations.py)（第 165 行）、[routes_pages.py](code/backend/app/api/routes_pages.py) `list_pages`/`get_page`（第 74、98 行）。
  - 网关路由改为 `GET /p/{conversation_id}/{page_id}`：按 `page_id` 取节点后校验 `page.conversation_id == conversation_id`，不符则 404。
  - 代理无需改：Next.js `"/p/:path*"`、Nginx `location /p/` 均为前缀/通配转发，多段路径命中。
  - 前端无需改：仅消费服务端返回的 `page_url`，`toAbsoluteUrl` 对多段路径同样工作（已确认 page.tsx 无硬编码 `/p/` 构造）。
- 删会话级联：
  - [delete_conversation](code/backend/app/api/routes_conversations.py)（第 94-105 行）置 `Conversation.deleted_at` 时，批量软删其下未删 `Page`（`UPDATE pages SET deleted_at=now WHERE conversation_id=... AND deleted_at IS NULL`）。网关已检查 `page.deleted_at`，级联后自动 404。
  - 防御冗余：`serve_page` 额外回看所属会话，会话已删也 404。

### 6. 文档沉淀
- `wiki/`：新增"生成页 JS 沙箱与安全"知识条（隔离模型、两道墙、`connect-src` 推理、roadmap）。
- `doc/20260602/`：本轮实施记录（管理面），并把本 plan 归档一份到此目录便于回溯。
- 更新 [code/backend/README.md](code/backend/README.md)、[wiki/for_human/1_整体架构.md](wiki/for_human/1_整体架构.md)，并在 [html-page-builder-architecture-plan.md](doc/20260522/html-page-builder-architecture-plan.md) 标注"已用沙箱方式支持 JS"（原文第一版禁 JS 已调整）。

## 后续阶段（本轮只留接缝，不实装）

- 阶段 1 一次性多文件 bundle：`manifest.json`（文件列表/类型/entry）、`StorageProvider` 扩展二进制+列举、`PageVersion.storage_key` 由单文件改为 version 前缀、网关按路径路由 + content-type、prompt 多文件协议。
- 阶段 2 agentic 工作区：`list/read/write/delete/rename` 文件工具 + 多轮迭代编辑，"续写"进化为对已有文件树的增量 diff。
- 安全加固：独立内容域名 `*.usercontent` + 通配证书（顶层文档不再仅靠 sandbox 头）、按页 opt-in 的 scoped `connect-src` 放开 live 数据、内容审核 + 举报 + 下架（`pages.status=suspended` 已预留）。

## 验证

- 生成含动画/tab 的页，确认预览与"打开页面"都能跑 JS 且布局正常。
- 用一段会 `fetch` 外部地址或弹 `document.cookie` 的 prompt 验证：网络被 `connect-src 'none'` 拦截、读不到主站 Cookie。
- 老页面（无 JS）用统一 interactive CSP 仍能正常渲染。
- 新生成页的分享链接为 `/p/{conversation_id}/{page_id}`，预览与"打开页面"均可访问；伪造不匹配的 conversation_id 返回 404。
- 删除会话后，其下节点 `/p/...` 链接返回 404。