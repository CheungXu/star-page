# 为展示页安全放开 CSS/JS（沙箱化单文件）实施记录

## 背景与定位

产品定位为"替代 Word/PPT 的 AI 富展示页"，JS/CSS 仅用于让展示更美观、交互更丰富，不做成真正的应用。第一版架构曾限制"生成页禁用 JS、靠清洗去掉危险代码"。本轮在保持纯静态、单文件形态下，把安全范式从"清洗掉 JS"切换为"隔离优先"，从而安全地放开 CSS/JS。

预研阶段，不背历史包袱：不为老数据保留双档、不引入 `render_mode` 字段、不加数据库迁移；所有生成页统一升级为可跑 JS 的沙箱页（老页本就无 JS，用更宽的 CSP 渲染无害）。

## 关键决策

- 运行边界：纯静态优先（OSS+CDN，不跑服务端）。
- 生成演进：本轮仍单文件、只放开 CSS/JS；下一步一次性多文件；最后到 agentic 工作区。
- 隔离机制：CSP `sandbox` + sandboxed iframe（无需新域名）。
- 外部网络：默认禁网（`connect-src 'none'`）+ 可信 CDN 白名单；未来按页 opt-in 受控放开。
- 安全分析详见 `wiki/generated-page-js-sandbox-and-security.md`。

## 本轮交付

1. 生成 Prompt：改写为"自包含富展示页"，允许展示型内联 JS（动画/tab/轮播/图表/折叠），禁止对外网络请求、真实表单提交、iframe/object/embed/base，确需库时仅可信 CDN。
2. HTML 清洗：`sanitize_html` 转为展示安全白名单——放行 script/事件属性/form/canvas/svg；移除 iframe/object/embed/base、meta refresh；外链 `<script src>` 仅留可信 CDN。
3. 页面 CSP：`/p` 网关统一输出 `sandbox allow-scripts ...` + `connect-src 'none'` + 脚本/样式 `'unsafe-inline'` + 可信 CDN 的展示型 CSP；新增配置 `GENERATED_PAGE_CDN_ALLOWLIST`（默认 jsdelivr/unpkg），供清洗与 CSP 共用。
4. 前端预览：预览 iframe 增加 `sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"`；缩放逻辑按容器尺寸反算、不读 contentDocument，跨域不受影响。

## 顺带优化：页面访问 URL 适配树逻辑 + 删会话级联

多模型生成树重构后，页面访问网关有两处没跟上，本轮一并处理：

- URL 层级化：`/p/{page_id}` → `/p/{conversation_id}/{page_id}`，体现"会话→节点"归属。新增统一助手 `app/core/urls.build_page_url`，替换 `generation_service`、`routes_conversations`、`routes_pages` 全部 URL 构造点；网关按 `page_id` 取节点后校验归属会话，不符返回 404。代理（Next.js `"/p/:path*"`、Nginx `location /p/`）与前端（仅消费服务端 `page_url`）均无需改。
- 删会话级联：`delete_conversation` 置 `Conversation.deleted_at` 时，批量软删其下未删 `Page`；网关额外回看所属会话，会话已删也 404。修复"删会话后节点分享链接仍可公开访问"。

## 验证

- 字节编译通过；本地用 venv 实测清洗器：内联脚本保留、jsdelivr 外链保留、evil 外链移除、onclick 保留、iframe/object 移除、form 保留、meta refresh 移除。
- CSP 头与 `build_page_url` 输出符合预期（`/p/{conversation_id}/{page_id}`）。
- 待线上联调项：生成含动画/tab 页确认预览与"打开页面"可跑 JS；构造 `fetch`/弹 `document.cookie` 的页面确认被 `connect-src 'none'` 拦、读不到主站 Cookie；伪造不匹配 conversation_id 返回 404；删会话后节点链接 404。

## 部署与运维

- 后端常驻服务直接从仓库跑 uvicorn 且未开 `--reload`，改 prompt 后一度仍是旧行为（模型思考仍声称"禁止 JS"）；执行 `systemctl restart star-page-backend.service` 后新 prompt 与新 CSP 生效。已把"改后端必须重启"写入 `code/backend/README.md` 与 `wiki/systemd-nextjs-fastapi-deployment.md`。
- 前端 iframe `sandbox` 属性需 `next build` + 重启 `star-page-frontend.service` 才上线（页面 CSP 由后端响应头下发，安全不依赖前端）；已重建并验证首页 200、`/_next/static/*.css` 返回 `200 text/css`。
- 后端 SSE 长连接会让 `systemctl restart` 等到默认 90s 才 SIGKILL；已给后端服务加 `TimeoutStopSec=10s`（模板与线上 unit 同步）。

## 后续阶段（本轮只留接缝，未实装）

- 阶段 1 一次性多文件 bundle：`manifest.json`、`StorageProvider` 扩展二进制+列举、`PageVersion.storage_key` 由单文件改为 version 前缀、网关按路径路由 + content-type、prompt 多文件协议。
- 阶段 2 agentic 工作区：`list/read/write/delete/rename` 文件工具 + 多轮迭代编辑，续写进化为对文件树的增量 diff。
- 安全加固：独立内容域名 `*.usercontent` + 通配证书、按页 opt-in 的 scoped `connect-src`、内容审核 + 举报 + 下架。

## 关联

- 方案归档：本目录 `generated_page_js_sandbox.plan.md`。
- 知识沉淀：`wiki/generated-page-js-sandbox-and-security.md`。
